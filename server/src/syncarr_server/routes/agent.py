from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.auth import require_agent_auth
from syncarr_server.config import Settings, get_settings
from syncarr_server.db import get_session
from syncarr_server.models import Asset, Assignment, Client
from syncarr_server.resolver import gc_orphaned_assets
from syncarr_server.schemas import (
    AgentAssignmentSchema,
    AgentAssignmentsResponse,
    AgentAssignmentsStats,
    AgentAssignmentState,
    AgentConfirmRequest,
    AgentConfirmResponse,
)

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
    if asset.status != "ready":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    file_path = asset.cache_path if asset.cache_path is not None else asset.source_path
    if not Path(file_path).is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    return FileResponse(file_path, media_type="application/octet-stream")


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
        await session.delete(assignment)
        await gc_orphaned_assets(session)
        await session.commit()
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

    if payload.actual_sha256 != asset.sha256 or payload.actual_size_bytes != asset.size_bytes:
        return AgentConfirmResponse(
            ok=False,
            reason="checksum_mismatch",
            expected_sha256=asset.sha256,
            actual_sha256=payload.actual_sha256,
        )

    assignment.state = "delivered"
    assignment.delivered_at = _utc_now()
    await session.commit()
    return AgentConfirmResponse(ok=True)

