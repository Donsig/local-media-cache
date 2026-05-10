from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from syncarr_server.models import Asset, Assignment, Client

PipelineStatus = Literal["queued", "transferring", "ready", "failed"]
PipelineSubstate = Literal[
    "transcoding_pending",
    "transcoding",
    "waiting_for_agent",
    "agent_offline",
    "downloading",
    "verifying",
    "stalled",
    "delivered",
    "transcode_failed",
]


@dataclass(frozen=True)
class RateSample:
    at: datetime
    bytes_downloaded: int


@dataclass(frozen=True)
class PipelineProjection:
    visible: bool
    status: PipelineStatus | None = None
    substate: PipelineSubstate | None = None
    detail: str | None = None
    bytes_downloaded: int | None = None
    size_bytes: int | None = None
    transfer_rate_bps: float | None = None
    eta_seconds: float | None = None


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _is_client_offline(client: Client | None, now: datetime, poll_interval_seconds: int) -> bool:
    if client is None:
        return False
    if client.last_seen is None:
        return True
    threshold = timedelta(seconds=max(3 * poll_interval_seconds, 180))
    return (now - _utc(client.last_seen)) > threshold


def _waiting_detail(asset: Asset, now: datetime) -> str:
    base = "waiting for agent to pick up"
    if asset.ready_at is None:
        return base
    elapsed = (now - _utc(asset.ready_at)).total_seconds()
    if elapsed < 300:
        return base
    if elapsed < 3600:
        return f"{base} · {int(elapsed / 60)}m"
    if elapsed < 86400:
        return f"{base} · {int(elapsed / 3600)}h"
    return f"{base} · {int(elapsed / 86400)}d"


def _is_stalled(assignment: Assignment, now: datetime, poll_interval_seconds: int) -> bool:
    if assignment.bytes_downloaded_updated_at is None:
        return False
    threshold = timedelta(seconds=max(2 * poll_interval_seconds, 120))
    return (now - _utc(assignment.bytes_downloaded_updated_at)) > threshold


def _compute_rate(samples: Sequence[RateSample]) -> float | None:
    if len(samples) < 2:
        return None
    span = (samples[-1].at - samples[0].at).total_seconds()
    if span <= 1.0:
        return None
    return (samples[-1].bytes_downloaded - samples[0].bytes_downloaded) / span


def _confirm_error_detail(
    assignment: Assignment,
    substate: PipelineSubstate,
    now: datetime,
    window_seconds: int,
) -> str | None:
    if assignment.last_confirm_error_at is None:
        return None
    if substate in ("stalled", "agent_offline"):
        return None
    if (now - _utc(assignment.last_confirm_error_at)) > timedelta(seconds=window_seconds):
        return None
    reason = assignment.last_confirm_error_reason or "unknown"
    if substate == "downloading":
        return f"last attempt failed: {reason} — retrying"
    if substate == "verifying":
        return f"verifying after recent {reason}"
    return None


def project(
    asset: Asset,
    assignment: Assignment | None,
    client: Client | None,
    *,
    now: datetime,
    poll_interval_seconds: int,
    rate_samples: Sequence[RateSample] = (),
    confirm_error_recent_window_seconds: int = 3600,
) -> PipelineProjection:
    if assignment is None:
        return PipelineProjection(visible=False)

    if assignment.state == "evict" or assignment.evict_requested_at is not None:
        return PipelineProjection(visible=False)

    if assignment.state == "delivered":
        return PipelineProjection(
            visible=True,
            status="ready",
            substate="delivered",
            bytes_downloaded=assignment.bytes_downloaded,
            size_bytes=asset.size_bytes,
        )

    if asset.status == "failed":
        return PipelineProjection(
            visible=True,
            status="failed",
            substate="transcode_failed",
            detail=f"transcode failed: {asset.status_detail or 'unknown error'}",
            size_bytes=asset.size_bytes,
        )

    if asset.status == "queued":
        return PipelineProjection(
            visible=True,
            status="queued",
            substate="transcoding_pending",
            detail="waiting to transcode",
            size_bytes=asset.size_bytes,
        )

    if asset.status == "transcoding":
        return PipelineProjection(
            visible=True,
            status="queued",
            substate="transcoding",
            detail="transcoding on server",
            size_bytes=asset.size_bytes,
        )

    if _is_client_offline(client, now, poll_interval_seconds):
        if client is not None and client.last_seen is not None:
            elapsed_mins = int((now - _utc(client.last_seen)).total_seconds() / 60)
            detail = f"agent offline (last seen {elapsed_mins}m ago)"
        else:
            detail = "agent offline (never seen)"
        return PipelineProjection(
            visible=True,
            status="queued",
            substate="agent_offline",
            detail=detail,
            bytes_downloaded=assignment.bytes_downloaded,
            size_bytes=asset.size_bytes,
        )

    raw_bytes = assignment.bytes_downloaded
    clamped = max(0, raw_bytes) if raw_bytes is not None else None

    if asset.size_bytes is None:
        return PipelineProjection(
            visible=True,
            status="queued",
            substate="waiting_for_agent",
            detail=_waiting_detail(asset, now),
            bytes_downloaded=clamped,
        )

    if clamped is None or clamped <= 0:
        return PipelineProjection(
            visible=True,
            status="queued",
            substate="waiting_for_agent",
            detail=_waiting_detail(asset, now),
            bytes_downloaded=clamped,
            size_bytes=asset.size_bytes,
        )

    if clamped >= asset.size_bytes:
        base_detail = "verifying size" if asset.sha256 is None else "verifying checksum"
        error_detail = _confirm_error_detail(
            assignment,
            "verifying",
            now,
            confirm_error_recent_window_seconds,
        )
        return PipelineProjection(
            visible=True,
            status="transferring",
            substate="verifying",
            detail=error_detail or base_detail,
            bytes_downloaded=clamped,
            size_bytes=asset.size_bytes,
        )

    if _is_stalled(assignment, now, poll_interval_seconds):
        threshold_secs = max(2 * poll_interval_seconds, 120)
        return PipelineProjection(
            visible=True,
            status="transferring",
            substate="stalled",
            detail=f"stalled — no progress in {threshold_secs // 60}m",
            bytes_downloaded=clamped,
            size_bytes=asset.size_bytes,
        )

    rate_bps = _compute_rate(rate_samples)
    eta: float | None = None
    if rate_bps is not None and rate_bps > 0:
        eta = (asset.size_bytes - clamped) / rate_bps

    download_detail = _confirm_error_detail(
        assignment,
        "downloading",
        now,
        confirm_error_recent_window_seconds,
    )

    return PipelineProjection(
        visible=True,
        status="transferring",
        substate="downloading",
        detail=download_detail,
        bytes_downloaded=clamped,
        size_bytes=asset.size_bytes,
        transfer_rate_bps=rate_bps,
        eta_seconds=eta,
    )
