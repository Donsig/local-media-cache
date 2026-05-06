"""Behavioral tests for reconciler.py."""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from syncarr_agent.aria2_client import DownloadStatus
from syncarr_agent.client import AssignmentItem
from syncarr_agent.reconciler import reconcile
from syncarr_agent.state import DownloadRecord

SERVER_TOKEN = "test-token"


def _assignment(
    *,
    asset_id: int = 1234,
    state: str = "ready",
    filename: str = "Bluey - S02E01 - Dance Mode.mkv",
    sha256: str | None = "expected-sha256",
    size_bytes: int | None = 1024,
    download_url: str | None = "http://server:8000/download/1234",
) -> AssignmentItem:
    return AssignmentItem(
        asset_id=asset_id,
        state=state,
        source_media_id="source-1234",
        filename=filename,
        sha256=sha256,
        size_bytes=size_bytes,
        download_url=download_url,
    )


def _local_path(library_root: Path, assignment: AssignmentItem) -> Path:
    return library_root / str(assignment.asset_id) / assignment.filename


def _write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _add_record(mock_state, assignment: AssignmentItem, local_path: Path, gid: str, status: str = "active") -> None:
    mock_state.add_record(
        DownloadRecord(
            asset_id=assignment.asset_id,
            gid=gid,
            local_path=local_path,
            status=status,
            started_at="2026-01-01T00:00:00+00:00",
        )
    )


def _run_reconcile(assignments, mock_state, mock_aria2, mock_server, tmp_library_root: Path) -> None:
    reconcile(
        assignments=assignments,
        state=mock_state,
        aria2=mock_aria2,
        server=mock_server,
        library_root=tmp_library_root,
        server_token=SERVER_TOKEN,
        log=structlog.get_logger(),
    )


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
            "filename": assignment.filename,
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
    mock_aria2.set_status("gid001", DownloadStatus.ACTIVE)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_aria2._add_calls == []
    assert mock_server.delivered_confirms == []


def test_ready_complete_sha256_match_confirms_delivered(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    content = b"verified media bytes"
    assignment = _assignment(sha256=_sha256(content), size_bytes=len(content))
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, content)
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.COMPLETE)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_server.delivered_confirms == [
        {
            "asset_id": assignment.asset_id,
            "sha256": assignment.sha256,
            "size_bytes": len(content),
        }
    ]
    assert mock_state.deleted == [assignment.asset_id]


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
    assert mock_aria2._add_calls[0]["filename"] == assignment.filename
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


def test_ready_aria2_error_sets_failed(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.ERROR)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.set_failed_calls == [assignment.asset_id]
    assert mock_server.delivered_confirms == []


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
