"""Behavioral tests for reconciler.py."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import structlog

from syncarr_agent.aria2_client import DownloadInfo, DownloadStatus
from syncarr_agent.client import AssignmentItem, ReconcileResponse, ServerClient
from syncarr_agent.reconciler import reconcile, run_reconcile
from syncarr_agent.state import DownloadRecord, StateDB

SERVER_TOKEN = "test-token"


def _assignment(
    *,
    asset_id: int = 1234,
    state: str = "ready",
    relative_path: str = "Bluey (2018)/Season 2/Bluey - S02E01 - Dance Mode.mkv",
    sha256: str | None = "expected-sha256",
    size_bytes: int | None = 1024,
    download_url: str | None = "http://server:8000/download/1234",
) -> AssignmentItem:
    return AssignmentItem(
        asset_id=asset_id,
        state=state,
        source_media_id="source-1234",
        relative_path=relative_path,
        sha256=sha256,
        size_bytes=size_bytes,
        download_url=download_url,
    )


def _local_path(library_root: Path, assignment: AssignmentItem) -> Path:
    return library_root / assignment.relative_path


def _write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _add_record(
    mock_state,
    assignment: AssignmentItem,
    local_path: Path,
    gid: str,
    status: str = "active",
) -> None:
    mock_state.add_record(
        DownloadRecord(
            asset_id=assignment.asset_id,
            gid=gid,
            local_path=local_path,
            status=status,
            started_at="2026-01-01T00:00:00+00:00",
        )
    )


def _run_reconcile(
    assignments,
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    reconcile(
        assignments=assignments,
        state=mock_state,
        aria2=mock_aria2,
        server=mock_server,
        library_root=tmp_library_root,
        server_token=SERVER_TOKEN,
        log=structlog.get_logger(),
    )


def _real_state(tmp_path: Path) -> StateDB:
    return StateDB(tmp_path / "state.db")


def _attach_progress_tracking(server: Any) -> None:
    server.progress_reports = []

    def report_progress(asset_id: int, bytes_downloaded: int) -> None:
        server.progress_reports.append((asset_id, bytes_downloaded))

    server.report_progress = report_progress


class _ReconcileServer:
    def __init__(
        self,
        *,
        response: ReconcileResponse | None = None,
    ) -> None:
        self.response = response or ReconcileResponse([], [])
        self.reconcile_calls: list[list[int]] = []
        self.delivered_confirms: list[dict[str, Any]] = []
        self.evicted_confirms: list[int] = []

    def reconcile(self, assets_present: list[int]) -> ReconcileResponse:
        self.reconcile_calls.append(assets_present)
        return self.response

    def confirm_delivered(self, asset_id: int, sha256: str, size_bytes: int) -> bool:
        self.delivered_confirms.append(
            {"asset_id": asset_id, "sha256": sha256, "size_bytes": size_bytes}
        )
        return True

    def confirm_evicted(self, asset_id: int) -> None:
        self.evicted_confirms.append(asset_id)


def test_queued_assignment_is_noop(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment(state="queued", sha256=None, size_bytes=None, download_url=None)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_aria2._add_calls == []
    assert mock_server.delivered_confirms == []


def test_ready_no_state_no_file_queues_download(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment(sha256="abc123", size_bytes=4096)
    local_path = _local_path(tmp_library_root, assignment)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_aria2._add_calls == [
        {
            "url": assignment.download_url,
            "filename": local_path.name,
            "directory": local_path.parent,
            "sha256": assignment.sha256,
            "auth_token": SERVER_TOKEN,
            "gid": "gid001",
        }
    ]
    assert mock_state.upserted == [
        {
            "asset_id": assignment.asset_id,
            "gid": "gid001",
            "local_path": local_path,
            "status": "active",
        }
    ]


def test_ready_active_download_is_noop(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _add_record(mock_state, assignment, local_path, "gid001")
    _attach_progress_tracking(mock_server)
    mock_aria2.set_status("gid001", DownloadStatus.ACTIVE)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_aria2._add_calls == []
    assert mock_server.delivered_confirms == []


def test_handle_ready_active_reports_progress(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _add_record(mock_state, assignment, local_path, "gid001")
    _attach_progress_tracking(mock_server)
    mock_aria2._statuses["gid001"] = DownloadInfo(
        gid="gid001",
        status=DownloadStatus.ACTIVE,
        completed_length=1024,
        total_length=4096,
    )

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_server.progress_reports == [(assignment.asset_id, 1024)]


def test_handle_ready_active_zero_bytes_no_report(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _add_record(mock_state, assignment, local_path, "gid001")
    _attach_progress_tracking(mock_server)
    mock_aria2._statuses["gid001"] = DownloadInfo(
        gid="gid001",
        status=DownloadStatus.ACTIVE,
        completed_length=0,
        total_length=4096,
    )

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_server.progress_reports == []


def test_ready_complete_sha256_match_confirms_delivered(
    mock_aria2,
    mock_server,
    tmp_path: Path,
    tmp_library_root: Path,
) -> None:
    content = b"verified media bytes"
    assignment = _assignment(sha256=_sha256(content), size_bytes=len(content))
    local_path = _local_path(tmp_library_root, assignment)
    state = _real_state(tmp_path)
    _write_file(local_path, content)
    state.upsert(assignment.asset_id, "gid001", local_path, status="active")
    mock_aria2.set_status("gid001", DownloadStatus.COMPLETE)

    _run_reconcile([assignment], state, mock_aria2, mock_server, tmp_library_root)

    assert mock_server.delivered_confirms == [
        {
            "asset_id": assignment.asset_id,
            "sha256": assignment.sha256,
            "size_bytes": len(content),
        }
    ]
    record = state.get(assignment.asset_id)
    assert record is not None
    assert record.status == "delivered"


def test_ready_complete_sha256_mismatch_requeues(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    content = b"corrupt media bytes"
    assignment = _assignment(sha256=_sha256(b"expected media bytes"))
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, content)
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.COMPLETE)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert not local_path.exists()
    assert len(mock_aria2._add_calls) == 1
    assert mock_aria2._add_calls[0]["url"] == assignment.download_url
    assert mock_aria2._add_calls[0]["filename"] == local_path.name
    assert mock_aria2._add_calls[0]["sha256"] == assignment.sha256
    assert mock_aria2._add_calls[0]["auth_token"] == SERVER_TOKEN
    assert mock_state.upserted == [
        {
            "asset_id": assignment.asset_id,
            "gid": "gid001",
            "local_path": local_path,
            "status": "active",
        }
    ]


def test_ready_complete_server_confirm_mismatch_requeues(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    content = b"valid media bytes"
    assignment = _assignment(sha256=_sha256(content), size_bytes=len(content))
    local_path = _local_path(tmp_library_root, assignment)
    mock_server._confirm_delivered_result = False
    _write_file(local_path, content)
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.COMPLETE)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert not local_path.exists()
    assert mock_state.deleted == [assignment.asset_id]
    assert mock_aria2._add_calls == []


def test_ready_aria2_error_with_file_clears_state_for_recovery(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """aria2 ERROR + file present -> clear state + delete .aria2 control file.
    Crash-recovery on the next poll will confirm delivery (if complete) or re-queue."""
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, b"partial download")
    control_file = local_path.parent / (local_path.name + ".aria2")
    _write_file(control_file, b"aria2 control data")
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.ERROR)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.deleted == [assignment.asset_id]
    assert mock_state.set_failed_calls == []
    assert not control_file.exists()
    assert local_path.exists()  # .mkv preserved; crash-recovery decides on next poll


def test_ready_stale_failed_no_file_auto_clears(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """Stale 'failed' record with no file on disk -> auto-clear, re-queue next poll."""
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    # File does NOT exist -- stale state from prior test run
    _add_record(mock_state, assignment, local_path, "old-gid", status="failed")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    # State cleared; no set_failed, no add_download (re-queue happens next poll)
    assert mock_state.deleted == [assignment.asset_id]
    assert mock_state.set_failed_calls == []
    assert mock_aria2._add_calls == []


def test_ready_aria2_error_no_file_clears_state(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """aria2 ERROR + file absent -> clear state (re-queue next poll), not set_failed."""
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    # File does NOT exist
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.ERROR)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.deleted == [assignment.asset_id]
    assert mock_state.set_failed_calls == []
    assert mock_aria2._add_calls == []  # re-queue is on next poll, not this one


def test_ready_failed_with_file_self_heals(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """Failed record + file present -> clear state + delete .aria2 control file.
    Crash-recovery on the next poll will confirm delivery (if complete) or re-queue."""
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, b"partial download - self heal scenario")
    control_file = local_path.parent / (local_path.name + ".aria2")
    _write_file(control_file, b"aria2 control data")
    _add_record(mock_state, assignment, local_path, "old-gid", status="failed")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.deleted == [assignment.asset_id]
    assert not control_file.exists()
    assert local_path.exists()  # .mkv preserved; crash-recovery decides on next poll


def test_ready_complete_but_missing_file_requeues(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """aria2 COMPLETE but file absent -> delete state (re-queue next poll)."""
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    # File does NOT exist
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.COMPLETE)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.deleted == [assignment.asset_id]
    assert mock_server.delivered_confirms == []
    assert mock_aria2._add_calls == []  # re-queue on next poll


def test_ready_stale_gid_clears_state(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.OTHER)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.deleted == [assignment.asset_id]
    assert mock_aria2._add_calls == []


def test_ready_crash_recovery_file_matches_confirms_without_aria2(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    content = b"recovered media bytes"
    assignment = _assignment(sha256=_sha256(content), size_bytes=len(content))
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, content)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_aria2._add_calls == []
    assert mock_server.delivered_confirms == [
        {
            "asset_id": assignment.asset_id,
            "sha256": assignment.sha256,
            "size_bytes": len(content),
        }
    ]
    assert mock_state.get(assignment.asset_id) == DownloadRecord(
        asset_id=assignment.asset_id,
        gid="crash-recovery",
        local_path=local_path,
        status="delivered",
        started_at="2026-01-01T00:00:00+00:00",
    )


def test_crash_recovery_passthrough_complete_confirms(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """Passthrough crash-recovery: file exists with matching size -> confirm delivery."""
    content = b"complete passthrough file"
    assignment = _assignment(sha256=None, size_bytes=len(content))
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, content)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_server.delivered_confirms != []
    assert mock_server.delivered_confirms[0]["asset_id"] == assignment.asset_id
    assert mock_server.delivered_confirms[0]["sha256"] == ""
    assert mock_aria2._add_calls == []


def test_crash_recovery_passthrough_size_mismatch_deletes_and_requeues(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """Passthrough crash-recovery: file present but smaller than expected -> delete + re-queue."""
    content = b"partial passthrough file"
    assignment = _assignment(sha256=None, size_bytes=len(content) + 1000)
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, content)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert not local_path.exists()
    assert mock_server.delivered_confirms == []
    assert mock_aria2._add_calls == []  # re-queue happens on next poll (no state + no file path)


def test_ready_crash_recovery_file_corrupt_deletes_and_requeues(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    content = b"corrupt recovered bytes"
    assignment = _assignment(sha256=_sha256(b"expected recovered bytes"))
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, content)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert not local_path.exists()
    assert len(mock_aria2._add_calls) == 1


def test_evict_with_active_download_removes_and_confirms(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment(state="evict", sha256=None, size_bytes=None, download_url=None)
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, b"media to evict")
    _add_record(mock_state, assignment, local_path, "gid001")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_aria2._remove_calls == ["gid001"]
    assert not local_path.exists()
    assert mock_server.evicted_confirms == [assignment.asset_id]
    assert mock_state.deleted == [assignment.asset_id]


def test_evict_no_state_no_file_still_confirms(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment(state="evict", sha256=None, size_bytes=None, download_url=None)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_server.evicted_confirms == [assignment.asset_id]


def test_evict_aria2_remove_raises_does_not_confirm(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment(state="evict", sha256=None, size_bytes=None, download_url=None)
    local_path = _local_path(tmp_library_root, assignment)
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2._remove_raises = Exception("rpc error")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_server.evicted_confirms == []
    assert mock_state.deleted == []


def test_download_uses_library_structure(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """Files land at library_root/relative_path, not library_root/<asset_id>/filename."""
    assignment = _assignment(
        relative_path="TV Shows/Bluey (2018)/Season 1/Bluey - S01E01.mkv"
    )

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    expected_dir = tmp_library_root / "TV Shows" / "Bluey (2018)" / "Season 1"
    assert mock_aria2._add_calls[0]["directory"] == expected_dir
    assert mock_aria2._add_calls[0]["filename"] == "Bluey - S01E01.mkv"


def test_evict_removes_empty_parent_dirs(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """After eviction, empty show/season dirs are cleaned up."""
    assignment = _assignment(
        state="evict",
        relative_path="TV Shows/Bluey (2018)/Season 1/Bluey - S01E01.mkv",
        sha256=None,
        size_bytes=None,
        download_url=None,
    )
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, b"media to evict")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert not local_path.exists()
    assert not local_path.parent.exists()  # Season 1 removed
    assert not (tmp_library_root / "TV Shows" / "Bluey (2018)").exists()  # show dir removed
    assert not (tmp_library_root / "TV Shows").exists()  # TV Shows dir removed
    assert tmp_library_root.exists()  # library root preserved


def test_evict_leaves_non_empty_parent_dirs(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """Walk-up stops at first non-empty dir."""
    assignment = _assignment(
        state="evict",
        relative_path="TV Shows/Bluey (2018)/Season 1/Bluey - S01E01.mkv",
        sha256=None,
        size_bytes=None,
        download_url=None,
    )
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, b"to evict")
    sibling = tmp_library_root / "TV Shows" / "Bluey (2018)" / "Season 1" / "S01E02.mkv"
    _write_file(sibling, b"keep this")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert not local_path.exists()
    assert local_path.parent.exists()  # Season 1 still has S01E02
    assert sibling.exists()


def test_confirm_delivered_sets_delivered_status(
    mock_aria2,
    mock_server,
    tmp_path: Path,
    tmp_library_root: Path,
) -> None:
    content = b"confirm updates state"
    assignment = _assignment(sha256=_sha256(content), size_bytes=len(content))
    local_path = _local_path(tmp_library_root, assignment)
    state = _real_state(tmp_path)
    _write_file(local_path, content)
    state.upsert(assignment.asset_id, "gid001", local_path, status="active")
    mock_aria2.set_status("gid001", DownloadStatus.COMPLETE)

    _run_reconcile([assignment], state, mock_aria2, mock_server, tmp_library_root)

    record = state.get(assignment.asset_id)
    assert record is not None
    assert record.status == "delivered"


def test_run_reconcile_reports_delivered_files(tmp_path: Path) -> None:
    state = _real_state(tmp_path)
    local_path = tmp_path / "library" / "show" / "episode.mkv"
    _write_file(local_path, b"delivered")
    state.upsert(55, "gid001", local_path, status="delivered")
    server = _ReconcileServer()

    run_reconcile(state, server, local_path.parent, structlog.get_logger())

    assert server.reconcile_calls == [[55]]


def test_run_reconcile_excludes_missing_file(tmp_path: Path) -> None:
    state = _real_state(tmp_path)
    local_path = tmp_path / "library" / "show" / "missing.mkv"
    state.upsert(55, "gid001", local_path, status="delivered")
    server = _ReconcileServer()

    run_reconcile(state, server, local_path.parent, structlog.get_logger())

    assert server.reconcile_calls == [[]]


def test_run_reconcile_deletes_orphan(tmp_path: Path) -> None:
    state = _real_state(tmp_path)
    local_path = tmp_path / "library" / "show" / "orphan.mkv"
    _write_file(local_path, b"orphan")
    control_file = local_path.parent / (local_path.name + ".aria2")
    _write_file(control_file, b"control")
    state.upsert(99, "gid001", local_path, status="delivered")
    server = _ReconcileServer(response=ReconcileResponse([99], []))

    run_reconcile(state, server, local_path.parent, structlog.get_logger())

    assert not local_path.exists()
    assert not control_file.exists()
    assert state.get(99) is None


def test_run_reconcile_clears_missing_record(tmp_path: Path) -> None:
    state = _real_state(tmp_path)
    local_path = tmp_path / "library" / "show" / "episode.mkv"
    state.upsert(10, "gid001", local_path, status="delivered")
    server = _ReconcileServer(response=ReconcileResponse([], [10]))

    run_reconcile(state, server, local_path.parent, structlog.get_logger())

    assert state.get(10) is None


def test_crash_recovery_confirm_writes_delivered_record(
    mock_aria2,
    mock_server,
    tmp_path: Path,
    tmp_library_root: Path,
) -> None:
    content = b"recovered media bytes"
    assignment = _assignment(sha256=_sha256(content), size_bytes=len(content))
    local_path = _local_path(tmp_library_root, assignment)
    state = _real_state(tmp_path)
    _write_file(local_path, content)

    _run_reconcile([assignment], state, mock_aria2, mock_server, tmp_library_root)

    record = state.get(assignment.asset_id)
    assert record is not None
    assert record.gid == "crash-recovery"
    assert record.status == "delivered"
    assert record.local_path == local_path


def test_handle_ready_stale_delivered_file_missing(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _add_record(mock_state, assignment, local_path, "gid001", status="delivered")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.deleted == [assignment.asset_id]
    assert len(mock_aria2._add_calls) == 1
    assert mock_aria2._add_calls[0]["url"] == assignment.download_url


def test_handle_ready_stale_delivered_file_present(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    content = b"still delivered"
    assignment = _assignment(sha256=_sha256(content), size_bytes=len(content))
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, content)
    _add_record(mock_state, assignment, local_path, "crash-recovery", status="delivered")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_server.delivered_confirms == [
        {
            "asset_id": assignment.asset_id,
            "sha256": assignment.sha256,
            "size_bytes": len(content),
        }
    ]
    assert mock_state.deleted == []
    assert mock_aria2._remove_calls == []
    assert mock_aria2._add_calls == []


def test_handle_ready_stale_delivered_passthrough_size_mismatch(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    content = b"wrong size passthrough"
    assignment = _assignment(sha256=None, size_bytes=len(content) + 1)
    local_path = _local_path(tmp_library_root, assignment)
    control_file = local_path.parent / (local_path.name + ".aria2")
    _write_file(local_path, content)
    _write_file(control_file, b"control")
    _add_record(mock_state, assignment, local_path, "crash-recovery", status="delivered")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert not local_path.exists()
    assert not control_file.exists()
    assert mock_server.delivered_confirms == []
    assert mock_state.deleted == [assignment.asset_id]
    assert len(mock_aria2._add_calls) == 1


def test_evict_delivered_record_skips_aria2(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment(state="evict", sha256=None, size_bytes=None, download_url=None)
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, b"delivered file")
    _add_record(mock_state, assignment, local_path, "crash-recovery", status="delivered")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_aria2._remove_calls == []
    assert not local_path.exists()
    assert mock_server.evicted_confirms == [assignment.asset_id]
    assert mock_state.deleted == [assignment.asset_id]


def test_server_client_reconcile() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/reconcile"
        assert request.method == "POST"
        assert json.loads(request.content.decode()) == {"assets_present": [1, 2]}
        return httpx.Response(
            200,
            json={
                "orphans_to_delete": [3],
                "missing_to_redownload": [4],
            },
        )

    client = ServerClient(
        "http://server:8000",
        "token",
        transport=httpx.MockTransport(handler),
    )

    result = client.reconcile([1, 2])

    assert result == ReconcileResponse(
        orphans_to_delete=[3],
        missing_to_redownload=[4],
    )
