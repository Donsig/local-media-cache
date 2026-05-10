from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from syncarr_server.models import Asset, Assignment, Client
from syncarr_server.pipeline import PipelineProjection, RateSample, project

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
POLL = 300


def make_asset(**kwargs: object) -> Asset:
    defaults: dict[str, object] = {
        "id": 1, "source_media_id": "media-1", "profile_id": "p1",
        "source_path": "/mnt/media/movie.mkv", "cache_path": None,
        "size_bytes": 1_000_000, "sha256": "abc123", "status": "ready",
        "status_detail": None, "created_at": datetime(2026, 1, 1, tzinfo=UTC), "ready_at": None,
    }
    defaults.update(kwargs)
    a = Asset()
    for k, v in defaults.items():
        setattr(a, k, v)
    return a


def make_assignment(**kwargs: object) -> Assignment:
    defaults: dict[str, object] = {
        "client_id": "client-1", "asset_id": 1, "state": "pending",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC), "delivered_at": None,
        "evict_requested_at": None, "bytes_downloaded": None,
        "bytes_downloaded_updated_at": None,
        "last_confirm_error_at": None, "last_confirm_error_reason": None,
    }
    defaults.update(kwargs)
    a = Assignment()
    for k, v in defaults.items():
        setattr(a, k, v)
    return a


def make_client(**kwargs: object) -> Client:
    defaults: dict[str, object] = {
        "id": "client-1", "name": "Caravan Pi", "auth_token": "tok",
        "storage_budget_bytes": None,
        "last_seen": NOW - timedelta(seconds=POLL // 2),
        "created_at": datetime(2026, 1, 1, tzinfo=UTC), "decommissioning": False,
    }
    defaults.update(kwargs)
    c = Client()
    for k, v in defaults.items():
        setattr(c, k, v)
    return c


def proj(**kwargs: object) -> PipelineProjection:
    asset = kwargs.pop("asset", make_asset())
    assignment = kwargs.pop("assignment", make_assignment())
    client = kwargs.pop("client", make_client())
    rate_samples = kwargs.pop("rate_samples", ())
    confirm_error_recent_window_seconds = kwargs.pop("confirm_error_recent_window_seconds", 3600)
    return project(
        asset, assignment, client,
        now=NOW, poll_interval_seconds=POLL,
        rate_samples=rate_samples,
        confirm_error_recent_window_seconds=confirm_error_recent_window_seconds,
    )


def test_row1_no_assignment_invisible() -> None:
    result = proj(assignment=None)
    assert result.visible is False
    assert result.status is None


def test_row2_evict_state_invisible() -> None:
    assert proj(assignment=make_assignment(state="evict")).visible is False


def test_row2_evict_requested_at_invisible() -> None:
    assert proj(assignment=make_assignment(evict_requested_at=NOW)).visible is False


def test_row2_both_evict_conditions_invisible() -> None:
    assert proj(assignment=make_assignment(state="evict", evict_requested_at=NOW)).visible is False


def test_row3_delivered_is_ready() -> None:
    result = proj(assignment=make_assignment(state="delivered"))
    assert result.visible is True
    assert result.status == "ready"
    assert result.substate == "delivered"
    assert result.detail is None


def test_row3_delivered_wins_over_asset_failed() -> None:
    result = proj(asset=make_asset(status="failed"), assignment=make_assignment(state="delivered"))
    assert result.status == "ready"
    assert result.substate == "delivered"


def test_row4_asset_failed() -> None:
    result = proj(asset=make_asset(status="failed", status_detail="ffmpeg exited with code 1"))
    assert result.status == "failed"
    assert result.substate == "transcode_failed"
    assert result.detail == "transcode failed: ffmpeg exited with code 1"


def test_row4_asset_failed_no_detail() -> None:
    assert proj(asset=make_asset(status="failed", status_detail=None)).detail == "transcode failed: unknown error"


def test_row5_asset_queued() -> None:
    result = proj(asset=make_asset(status="queued"))
    assert result.status == "queued"
    assert result.substate == "transcoding_pending"
    assert result.detail == "waiting to transcode"


def test_row6_asset_transcoding() -> None:
    result = proj(asset=make_asset(status="transcoding"))
    assert result.status == "queued"
    assert result.substate == "transcoding"
    assert result.detail == "transcoding on server"


def test_row7_client_offline_wins_over_rows_8_to_11() -> None:
    offline = make_client(last_seen=NOW - timedelta(seconds=3 * POLL + 1))
    result = proj(client=offline, assignment=make_assignment(bytes_downloaded=500_000))
    assert result.status == "queued"
    assert result.substate == "agent_offline"
    assert "offline" in (result.detail or "")


def test_row7_client_offline_last_seen_none() -> None:
    assert proj(client=make_client(last_seen=None)).substate == "agent_offline"


def test_row7_offline_threshold_not_stalled_at_boundary() -> None:
    # max(3*300, 180) = 900s — exactly at threshold is NOT offline
    assert proj(client=make_client(last_seen=NOW - timedelta(seconds=900))).substate != "agent_offline"


def test_row7_offline_one_second_over_threshold() -> None:
    assert proj(client=make_client(last_seen=NOW - timedelta(seconds=901))).substate == "agent_offline"


def test_row8_size_bytes_none_waiting_for_agent() -> None:
    result = proj(asset=make_asset(size_bytes=None))
    assert result.status == "queued"
    assert result.substate == "waiting_for_agent"


def test_row9_bytes_downloaded_none_waiting_for_agent() -> None:
    assert proj(assignment=make_assignment(bytes_downloaded=None)).substate == "waiting_for_agent"


def test_row9_bytes_downloaded_zero_agent_acknowledged() -> None:
    # bytes_downloaded=0 means the agent has the item in its aria2 queue
    # (acknowledged) — show as transferring, not waiting_for_agent.
    result = proj(assignment=make_assignment(bytes_downloaded=0))
    assert result.status == "transferring"
    assert result.substate == "downloading"


def test_row10_bytes_gte_size_verifying_with_sha256() -> None:
    result = proj(asset=make_asset(size_bytes=1_000_000, sha256="abc"), assignment=make_assignment(bytes_downloaded=1_000_000))
    assert result.status == "transferring"
    assert result.substate == "verifying"
    assert result.detail == "verifying checksum"
    assert result.transfer_rate_bps is None
    assert result.eta_seconds is None


def test_row10_bytes_gte_size_verifying_passthrough() -> None:
    result = proj(asset=make_asset(size_bytes=1_000_000, sha256=None), assignment=make_assignment(bytes_downloaded=1_000_000))
    assert result.substate == "verifying"
    assert result.detail == "verifying size"


def test_row10_bytes_over_size_treated_as_verifying() -> None:
    result = proj(asset=make_asset(size_bytes=1_000_000), assignment=make_assignment(bytes_downloaded=1_100_000))
    assert result.substate == "verifying"


def test_row11_stalled() -> None:
    stale = NOW - timedelta(seconds=601)
    result = proj(assignment=make_assignment(bytes_downloaded=500_000, bytes_downloaded_updated_at=stale))
    assert result.status == "transferring"
    assert result.substate == "stalled"
    assert "stalled" in (result.detail or "")


def test_row11_stalled_threshold_boundary_not_stalled() -> None:
    ts = NOW - timedelta(seconds=600)
    assert proj(assignment=make_assignment(bytes_downloaded=500_000, bytes_downloaded_updated_at=ts)).substate != "stalled"


def test_row11_stalled_one_second_over_threshold() -> None:
    ts = NOW - timedelta(seconds=601)
    assert proj(assignment=make_assignment(bytes_downloaded=500_000, bytes_downloaded_updated_at=ts)).substate == "stalled"


def test_row11_stalled_null_updated_at_not_stalled() -> None:
    assert proj(assignment=make_assignment(bytes_downloaded=500_000, bytes_downloaded_updated_at=None)).substate != "stalled"


def test_row12_downloading() -> None:
    result = proj(assignment=make_assignment(bytes_downloaded=250_000))
    assert result.status == "transferring"
    assert result.substate == "downloading"
    assert result.detail is None


def test_row12_downloading_with_rate() -> None:
    samples = [
        RateSample(at=NOW - timedelta(seconds=2), bytes_downloaded=100_000),
        RateSample(at=NOW, bytes_downloaded=300_000),
    ]
    result = proj(asset=make_asset(size_bytes=1_000_000), assignment=make_assignment(bytes_downloaded=300_000), rate_samples=samples)
    assert result.transfer_rate_bps == pytest.approx(100_000.0)
    assert result.eta_seconds == pytest.approx(7.0)


def test_row12_rate_zero_samples_is_none() -> None:
    result = proj(assignment=make_assignment(bytes_downloaded=250_000), rate_samples=())
    assert result.transfer_rate_bps is None
    assert result.eta_seconds is None


def test_row12_rate_one_sample_is_none() -> None:
    samples = [RateSample(at=NOW, bytes_downloaded=250_000)]
    assert proj(assignment=make_assignment(bytes_downloaded=250_000), rate_samples=samples).transfer_rate_bps is None


def test_row12_rate_two_samples_under_1s_is_none() -> None:
    samples = [
        RateSample(at=NOW - timedelta(milliseconds=500), bytes_downloaded=200_000),
        RateSample(at=NOW, bytes_downloaded=300_000),
    ]
    assert proj(assignment=make_assignment(bytes_downloaded=300_000), rate_samples=samples).transfer_rate_bps is None


def test_stalled_suppresses_rate_and_eta() -> None:
    ts = NOW - timedelta(seconds=601)
    samples = [RateSample(at=NOW - timedelta(seconds=2), bytes_downloaded=100_000), RateSample(at=NOW, bytes_downloaded=300_000)]
    result = proj(assignment=make_assignment(bytes_downloaded=300_000, bytes_downloaded_updated_at=ts), rate_samples=samples)
    assert result.substate == "stalled"
    assert result.transfer_rate_bps is None
    assert result.eta_seconds is None


def test_verifying_suppresses_rate_and_eta() -> None:
    samples = [RateSample(at=NOW - timedelta(seconds=2), bytes_downloaded=900_000), RateSample(at=NOW, bytes_downloaded=1_000_000)]
    result = proj(asset=make_asset(size_bytes=1_000_000), assignment=make_assignment(bytes_downloaded=1_000_000), rate_samples=samples)
    assert result.substate == "verifying"
    assert result.transfer_rate_bps is None


def test_bytes_negative_clamped_to_zero_agent_acknowledged() -> None:
    # Negative bytes_downloaded is clamped to 0 — treated as acknowledged by agent.
    result = proj(assignment=make_assignment(bytes_downloaded=-1))
    assert result.status == "transferring"
    assert result.substate == "downloading"


def test_confirm_error_downloading_within_window() -> None:
    error_at = NOW - timedelta(seconds=100)
    result = proj(assignment=make_assignment(bytes_downloaded=300_000, last_confirm_error_at=error_at, last_confirm_error_reason="checksum_mismatch"))
    assert result.substate == "downloading"
    assert result.detail == "last attempt failed: checksum_mismatch — retrying"


def test_confirm_error_verifying_within_window() -> None:
    error_at = NOW - timedelta(seconds=100)
    result = proj(asset=make_asset(size_bytes=1_000_000), assignment=make_assignment(bytes_downloaded=1_000_000, last_confirm_error_at=error_at, last_confirm_error_reason="size_mismatch"))
    assert result.substate == "verifying"
    assert result.detail == "verifying after recent size_mismatch"


def test_confirm_error_stalled_not_overridden() -> None:
    error_at = NOW - timedelta(seconds=100)
    stale_ts = NOW - timedelta(seconds=601)
    result = proj(assignment=make_assignment(bytes_downloaded=300_000, bytes_downloaded_updated_at=stale_ts, last_confirm_error_at=error_at, last_confirm_error_reason="checksum_mismatch"))
    assert result.substate == "stalled"
    assert "checksum_mismatch" not in (result.detail or "")


def test_confirm_error_outside_window_ignored() -> None:
    error_at = NOW - timedelta(seconds=3700)
    result = proj(assignment=make_assignment(bytes_downloaded=300_000, last_confirm_error_at=error_at, last_confirm_error_reason="checksum_mismatch"))
    assert result.substate == "downloading"
    assert result.detail is None


def test_bytes_downloaded_propagated_to_projection() -> None:
    assert proj(assignment=make_assignment(bytes_downloaded=400_000)).bytes_downloaded == 400_000


def test_size_bytes_propagated_to_projection() -> None:
    assert proj(asset=make_asset(size_bytes=2_000_000)).size_bytes == 2_000_000
