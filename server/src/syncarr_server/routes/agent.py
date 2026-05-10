from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.auth import require_agent_auth
from syncarr_server.config import Settings, get_settings
from syncarr_server.db import get_session
from syncarr_server.models import Asset, Assignment, Client
from syncarr_server.pipeline import RateSample
from syncarr_server.resolver import delete_cache_files, gc_orphaned_assets
from syncarr_server.schemas import (
    AgentAssignmentSchema,
    AgentAssignmentsResponse,
    AgentAssignmentsStats,
    AgentAssignmentState,
    AgentConfirmRequest,
    AgentConfirmResponse,
    AgentProgressRequest,
    ReconcileRequest,
    ReconcileResponse,
)
from syncarr_server.services.rate_tracker import rate_tracker

router = APIRouter(tags=["agent"])


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _relative_path(source_path: str, local_path_prefix: str) -> str:
    try:
        return str(Path(source_path).relative_to(local_path_prefix).as_posix())
    except ValueError:
        return Path(source_path).name


def _effective_state(assignment: Assignment, asset: Asset) -> AgentAssignmentState | None:
    if assignment.state == "delivered":
        return None
    if assignment.state == "evict":
        return "evict"
    if assignment.state == "pending" and asset.status == "ready":
        return "ready"
    return "queued"


def _assignment_schema(
    assignment: Assignment,
    asset: Asset,
    effective_state: AgentAssignmentState,
    rel_path: str,
) -> AgentAssignmentSchema:
    if effective_state != "ready":
        return AgentAssignmentSchema(
            asset_id=asset.id,
            state=effective_state,
            source_media_id=asset.source_media_id,
            relative_path=rel_path,
        )

    return AgentAssignmentSchema(
        asset_id=asset.id,
        state=effective_state,
        source_media_id=asset.source_media_id,
        relative_path=rel_path,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        download_url=f"/download/{assignment.asset_id}",
    )


async def _get_assignment_asset(
    session: AsyncSession,
    client_id: str,
    asset_id: int,
) -> tuple[Assignment, Asset] | None:
    result = await session.execute(
        select(Assignment, Asset)
        .join(Asset, Assignment.asset_id == Asset.id)
        .where(Assignment.client_id == client_id, Assignment.asset_id == asset_id),
    )
    row = result.one_or_none()
    if row is None:
        return None
    assignment, asset = row
    return assignment, asset


@router.get(
    "/assignments",
    response_model=AgentAssignmentsResponse,
    response_model_exclude_none=True,
)
async def list_assignments(
    client: Annotated[Client, Depends(require_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AgentAssignmentsResponse:
    now = _utc_now()
    await session.execute(
        update(Client).where(Client.id == client.id).values(last_seen=now),
    )

    result = await session.execute(
        select(Assignment, Asset)
        .join(Asset, Assignment.asset_id == Asset.id)
        .where(Assignment.client_id == client.id)
        .order_by(Assignment.created_at, Assignment.asset_id),
    )

    assignments: list[AgentAssignmentSchema] = []
    ready_count = 0
    queued_count = 0
    evict_count = 0
    total_assigned_bytes = 0

    for assignment, asset in result.all():
        effective_state = _effective_state(assignment, asset)
        if effective_state is None:
            continue

        if effective_state == "ready":
            ready_count += 1
            if asset.size_bytes is not None:
                total_assigned_bytes += asset.size_bytes
        elif effective_state == "queued":
            queued_count += 1
        else:
            evict_count += 1

        rel_path = _relative_path(asset.source_path, settings.local_path_prefix)
        assignments.append(_assignment_schema(assignment, asset, effective_state, rel_path))

    await session.commit()
    return AgentAssignmentsResponse(
        client_id=client.id,
        server_time=now,
        assignments=assignments,
        stats=AgentAssignmentsStats(
            total_assigned_bytes=total_assigned_bytes,
            ready_count=ready_count,
            queued_count=queued_count,
            evict_count=evict_count,
        ),
    )


@router.get("/download/{asset_id}")
async def download_asset(
    asset_id: int,
    client: Annotated[Client, Depends(require_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    assignment_asset = await _get_assignment_asset(session, client.id, asset_id)
    if assignment_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    _assignment, asset = assignment_asset
    if _assignment.evict_requested_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Assignment is pending eviction",
        )
    if asset.status != "ready":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    file_path = asset.cache_path if asset.cache_path is not None else asset.source_path
    if not Path(file_path).is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    return FileResponse(file_path, media_type="application/octet-stream")


@router.post(
    "/reconcile",
    response_model=ReconcileResponse,
)
async def reconcile_assignments(
    payload: ReconcileRequest,
    client: Annotated[Client, Depends(require_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReconcileResponse:
    present_set = set(payload.assets_present)

    delivered_result = await session.execute(
        select(Assignment).where(
            Assignment.client_id == client.id,
            Assignment.state == "delivered",
        ),
    )
    missing_to_redownload: list[int] = []
    for assignment in delivered_result.scalars():
        if assignment.asset_id in present_set:
            continue
        assignment.state = "pending"
        assignment.delivered_at = None
        missing_to_redownload.append(assignment.asset_id)

    active_result = await session.execute(
        select(Assignment.asset_id).where(
            Assignment.client_id == client.id,
            Assignment.state.in_(("pending", "delivered")),
        ),
    )
    active_ids = set(active_result.scalars())
    orphans_to_delete = [asset_id for asset_id in present_set if asset_id not in active_ids]

    await session.commit()
    return ReconcileResponse(
        orphans_to_delete=sorted(orphans_to_delete),
        missing_to_redownload=sorted(missing_to_redownload),
    )


@router.patch(
    "/assignments/{asset_id}/progress",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_assignment_progress(
    asset_id: int,
    payload: AgentProgressRequest,
    client: Annotated[Client, Depends(require_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    logger = structlog.get_logger()
    assignment_asset = await _get_assignment_asset(session, client.id, asset_id)
    if assignment_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    assignment, _asset = assignment_asset
    if assignment.state not in ("pending",):
        return

    new_bytes = payload.bytes_downloaded
    current = assignment.bytes_downloaded or 0
    now = datetime.now(UTC)

    if new_bytes > current:
        assignment.bytes_downloaded = new_bytes
        assignment.bytes_downloaded_updated_at = now
        rate_tracker.record(
            (client.id, asset_id),
            RateSample(at=now, bytes_downloaded=new_bytes),
        )
    elif new_bytes == 0 and assignment.bytes_downloaded is None:
        # First report from agent while item is in aria2's WAITING queue.
        # Mark as acknowledged (0 bytes) so the UI shows "transferring" rather
        # than "waiting for agent to pick up".
        assignment.bytes_downloaded = 0
        assignment.bytes_downloaded_updated_at = now
    elif new_bytes < current:
        logger.warning(
            "bytes_downloaded_decreased",
            client_id=client.id,
            asset_id=asset_id,
            current=current,
            received=new_bytes,
        )

    await session.commit()


@router.post(
    "/confirm/{asset_id}",
    response_model=AgentConfirmResponse,
    response_model_exclude_none=True,
)
async def confirm_asset(
    asset_id: int,
    payload: AgentConfirmRequest,
    client: Annotated[Client, Depends(require_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentConfirmResponse:
    assignment_asset = await _get_assignment_asset(session, client.id, asset_id)

    if payload.state == "evicted":
        if assignment_asset is None:
            return AgentConfirmResponse(ok=True)
        assignment, _asset = assignment_asset
        if assignment.evict_requested_at is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Assignment is not pending eviction",
            )
        await session.delete(assignment)
        paths_to_delete = await gc_orphaned_assets(session)
        await session.commit()
        delete_cache_files(paths_to_delete)
        return AgentConfirmResponse(ok=True)

    if assignment_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    assignment, asset = assignment_asset
    if assignment.state == "delivered":
        return AgentConfirmResponse(ok=True)

    if assignment.state == "evict":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assignment is pending eviction; confirm evicted instead",
        )
    if asset.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset is not ready for delivery confirmation",
        )

    now = datetime.now(UTC)

    # sha256-based check for transcoded assets
    if asset.sha256 is not None:
        if payload.actual_sha256 != asset.sha256 or payload.actual_size_bytes != asset.size_bytes:
            assignment.last_confirm_error_at = now
            assignment.last_confirm_error_reason = "checksum_mismatch"
            await session.commit()
            return AgentConfirmResponse(
                ok=False,
                reason="checksum_mismatch",
                expected_sha256=asset.sha256,
                actual_sha256=payload.actual_sha256,
            )
    # size-only check for passthrough assets (sha256=None)
    elif asset.size_bytes is not None and payload.actual_size_bytes != asset.size_bytes:
        assignment.last_confirm_error_at = now
        assignment.last_confirm_error_reason = "size_mismatch"
        await session.commit()
        return AgentConfirmResponse(
            ok=False,
            reason="size_mismatch",
            expected_sha256=None,
            actual_sha256=payload.actual_sha256,
        )

    assignment.state = "delivered"
    assignment.delivered_at = now
    assignment.last_confirm_error_at = None
    assignment.last_confirm_error_reason = None
    await session.commit()
    return AgentConfirmResponse(ok=True)
