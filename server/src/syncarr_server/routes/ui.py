from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.auth import require_ui_auth
from syncarr_server.config import get_settings
from syncarr_server.db import get_session
from syncarr_server.models import Asset, Assignment, Client, Profile, Subscription
from syncarr_server.pipeline import project
from syncarr_server.providers.base import MediaProvider
from syncarr_server.resolver import resolve_all_subscriptions
from syncarr_server.schemas import (
    AssetStatusSchema,
    ClientAssignmentSchema,
    ClientCreateRequest,
    ClientCreateResponse,
    ClientSchema,
    ClientsResponse,
    ClientUpdateRequest,
    QueueResponse,
    QueueRowSchema,
    ProfileCreateRequest,
    ProfileSchema,
    ProfilesResponse,
    ProfileUpdateRequest,
    SubscriptionCreateRequest,
    SubscriptionSchema,
    SubscriptionScopeType,
    SubscriptionsResponse,
    SubscriptionUpdateRequest,
    validate_subscription_scope,
)
from syncarr_server.services.rate_tracker import rate_tracker

router = APIRouter(tags=["ui"])

_PIPELINE_SORT_ORDER: dict[str, int] = {
    "transferring": 0,
    "queued": 1,
    "failed": 2,
    "ready": 3,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _provider(request: Request) -> MediaProvider:
    provider = getattr(request.app.state, "media_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media provider is not configured",
        )
    return cast(MediaProvider, provider)


def _client_schema(client: Client) -> ClientSchema:
    return ClientSchema(
        id=client.id,
        name=client.name,
        storage_budget_bytes=client.storage_budget_bytes,
        last_seen=client.last_seen,
        created_at=client.created_at,
        decommissioning=client.decommissioning,
    )


def _profile_schema(profile: Profile) -> ProfileSchema:
    return ProfileSchema(
        id=profile.id,
        name=profile.name,
        ffmpeg_args=profile.ffmpeg_args,
        target_size_bytes=profile.target_size_bytes,
        created_at=profile.created_at,
    )


def _subscription_schema(subscription: Subscription) -> SubscriptionSchema:
    scope_type = cast(SubscriptionScopeType, subscription.scope_type)
    return SubscriptionSchema(
        id=subscription.id,
        client_id=subscription.client_id,
        media_item_id=subscription.media_item_id,
        scope_type=scope_type,
        scope_params=subscription.scope_params,
        profile_id=subscription.profile_id,
        created_at=subscription.created_at,
    )


def _stored_scope_type(scope_type: SubscriptionScopeType) -> SubscriptionScopeType:
    if scope_type == "episode":
        return "movie"
    return scope_type


async def _get_client(session: AsyncSession, client_id: str) -> Client:
    client = await session.get(Client, client_id)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client {client_id!r} not found",
        )
    return client


async def _get_profile(session: AsyncSession, profile_id: str) -> Profile:
    profile = await session.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {profile_id!r} not found",
        )
    return profile


async def _get_subscription(session: AsyncSession, subscription_id: int) -> Subscription:
    subscription = await session.get(Subscription, subscription_id)
    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription {subscription_id!r} not found",
        )
    return subscription


@router.get(
    "/clients",
    response_model=ClientsResponse,
    dependencies=[Depends(require_ui_auth)],
)
async def list_clients(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClientsResponse:
    clients = list((await session.execute(select(Client).order_by(Client.created_at))).scalars())
    return ClientsResponse(clients=[_client_schema(client) for client in clients])


@router.post(
    "/clients",
    response_model=ClientCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_ui_auth)],
)
async def create_client(
    payload: ClientCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClientCreateResponse:
    if await session.get(Client, payload.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Client {payload.id!r} already exists",
        )

    client = Client(
        id=payload.id,
        name=payload.name,
        auth_token=f"agent-{payload.id}-{secrets.token_urlsafe(32)}",
        storage_budget_bytes=payload.storage_budget_bytes,
        last_seen=None,
        created_at=_utc_now(),
        decommissioning=False,
    )
    session.add(client)
    await session.commit()
    await resolve_all_subscriptions(provider=_provider(request), session=session)
    return ClientCreateResponse(**_client_schema(client).model_dump(), auth_token=client.auth_token)


@router.patch(
    "/clients/{client_id}",
    response_model=ClientSchema,
    dependencies=[Depends(require_ui_auth)],
)
async def update_client(
    client_id: str,
    payload: ClientUpdateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClientSchema:
    client = await _get_client(session, client_id)
    if payload.name is not None:
        client.name = payload.name
    if "storage_budget_bytes" in payload.model_fields_set:
        client.storage_budget_bytes = payload.storage_budget_bytes
    await session.commit()
    await resolve_all_subscriptions(provider=_provider(request), session=session)
    return _client_schema(client)


@router.delete(
    "/clients/{client_id}",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_ui_auth)],
)
async def delete_client(
    client_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    client = await _get_client(session, client_id)
    client.decommissioning = True
    subscription_result = await session.execute(
        select(Subscription).where(Subscription.client_id == client_id),
    )
    for subscription in list(subscription_result.scalars()):
        await session.delete(subscription)
    await session.commit()
    await resolve_all_subscriptions(provider=_provider(request), session=session)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.get(
    "/clients/{client_id}/assignments",
    response_model=list[ClientAssignmentSchema],
    response_model_exclude_none=True,
    dependencies=[Depends(require_ui_auth)],
)
async def list_client_assignments(
    client_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    media_item_ids: str = "",
) -> list[ClientAssignmentSchema]:
    client = await _get_client(session, client_id)
    settings = get_settings()
    now = datetime.now(UTC)

    ids = [id_.strip() for id_ in media_item_ids.split(",") if id_.strip()]

    query = (
        select(Assignment, Asset)
        .join(Asset, Assignment.asset_id == Asset.id)
        .where(Assignment.client_id == client_id)
        .order_by(Assignment.created_at, Assignment.asset_id)
    )
    if ids:
        query = query.where(Asset.source_media_id.in_(ids))

    result = await session.execute(query)

    assignments: list[ClientAssignmentSchema] = []
    for assignment, asset in result.all():
        samples = rate_tracker.samples_for((client.id, asset.id))
        p = project(
            asset,
            assignment,
            client,
            now=now,
            poll_interval_seconds=settings.agent_poll_interval_seconds,
            rate_samples=samples,
        )
        if not p.visible:
            continue
        if ids:
            assignments.append(
                ClientAssignmentSchema(
                    media_item_id=asset.source_media_id,
                    state="ready" if p.status == "ready" else "queued",
                )
            )
            continue
        assignments.append(
            ClientAssignmentSchema(
                media_item_id=asset.source_media_id,
                state="ready" if p.status == "ready" else "queued",
                asset_id=asset.id,
                profile_id=asset.profile_id,
                pipeline_status=p.status or "queued",
                pipeline_substate=p.substate,
                pipeline_detail=p.detail,
            )
        )
    return assignments


@router.get(
    "/profiles",
    response_model=ProfilesResponse,
    dependencies=[Depends(require_ui_auth)],
)
async def list_profiles(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfilesResponse:
    profiles = list((await session.execute(select(Profile).order_by(Profile.created_at))).scalars())
    return ProfilesResponse(profiles=[_profile_schema(profile) for profile in profiles])


@router.post(
    "/profiles",
    response_model=ProfileSchema,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_ui_auth)],
)
async def create_profile(
    payload: ProfileCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileSchema:
    if await session.get(Profile, payload.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Profile {payload.id!r} already exists",
        )

    profile = Profile(
        id=payload.id,
        name=payload.name,
        ffmpeg_args=payload.ffmpeg_args,
        target_size_bytes=payload.target_size_bytes,
        created_at=_utc_now(),
    )
    session.add(profile)
    await session.commit()
    await resolve_all_subscriptions(provider=_provider(request), session=session)
    return _profile_schema(profile)


@router.patch(
    "/profiles/{profile_id}",
    response_model=ProfileSchema,
    dependencies=[Depends(require_ui_auth)],
)
async def update_profile(
    profile_id: str,
    payload: ProfileUpdateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileSchema:
    profile = await _get_profile(session, profile_id)
    if payload.name is not None:
        profile.name = payload.name
    if "ffmpeg_args" in payload.model_fields_set:
        profile.ffmpeg_args = payload.ffmpeg_args
    if "target_size_bytes" in payload.model_fields_set:
        profile.target_size_bytes = payload.target_size_bytes
    await session.commit()
    await resolve_all_subscriptions(provider=_provider(request), session=session)
    return _profile_schema(profile)


@router.delete(
    "/profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_ui_auth)],
)
async def delete_profile(
    profile_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    profile = await _get_profile(session, profile_id)
    in_use = (
        await session.execute(
            select(Subscription.id).where(Subscription.profile_id == profile_id).limit(1),
        )
    ).scalar_one_or_none()
    if in_use is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Profile {profile_id!r} is still in use",
        )

    await session.delete(profile)
    await session.commit()
    await resolve_all_subscriptions(provider=_provider(request), session=session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/subscriptions",
    response_model=SubscriptionsResponse,
    dependencies=[Depends(require_ui_auth)],
)
async def list_subscriptions(
    session: Annotated[AsyncSession, Depends(get_session)],
    client_id: str | None = None,
) -> SubscriptionsResponse:
    query = select(Subscription).order_by(Subscription.created_at)
    if client_id is not None:
        query = query.where(Subscription.client_id == client_id)
    subscriptions = list((await session.execute(query)).scalars())
    return SubscriptionsResponse(
        subscriptions=[_subscription_schema(subscription) for subscription in subscriptions],
    )


@router.post(
    "/subscriptions",
    response_model=SubscriptionSchema,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_ui_auth)],
)
async def create_subscription(
    payload: SubscriptionCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubscriptionSchema:
    client = await _get_client(session, payload.client_id)
    if client.decommissioning:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Client {payload.client_id!r} is decommissioning",
        )
    await _get_profile(session, payload.profile_id)

    subscription = Subscription(
        client_id=payload.client_id,
        media_item_id=payload.media_item_id,
        scope_type=_stored_scope_type(payload.scope_type),
        scope_params=payload.scope_params,
        profile_id=payload.profile_id,
        created_at=_utc_now(),
    )
    session.add(subscription)
    await session.commit()
    await resolve_all_subscriptions(provider=_provider(request), session=session)
    return _subscription_schema(subscription)


@router.patch(
    "/subscriptions/{subscription_id}",
    response_model=SubscriptionSchema,
    dependencies=[Depends(require_ui_auth)],
)
async def update_subscription(
    subscription_id: int,
    payload: SubscriptionUpdateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubscriptionSchema:
    subscription = await _get_subscription(session, subscription_id)

    media_item_id = payload.media_item_id or subscription.media_item_id
    scope_type = cast(SubscriptionScopeType, payload.scope_type or subscription.scope_type)
    stored_scope_type = _stored_scope_type(scope_type)
    scope_params = (
        payload.scope_params
        if "scope_params" in payload.model_fields_set
        else subscription.scope_params
    )
    profile_id = payload.profile_id or subscription.profile_id
    validate_subscription_scope(scope_type, scope_params)
    await _get_profile(session, profile_id)

    subscription.media_item_id = media_item_id
    subscription.scope_type = stored_scope_type
    subscription.scope_params = scope_params
    subscription.profile_id = profile_id
    await session.commit()
    await resolve_all_subscriptions(provider=_provider(request), session=session)
    return _subscription_schema(subscription)


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_ui_auth)],
)
async def delete_subscription(
    subscription_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    subscription = await _get_subscription(session, subscription_id)
    await session.delete(subscription)
    await session.commit()
    await resolve_all_subscriptions(provider=_provider(request), session=session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_ui_auth)],
)
async def delete_asset(
    asset_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    subscriptions = list(
        (
            await session.execute(
                select(Subscription).where(Subscription.media_item_id == asset.source_media_id)
            )
        ).scalars()
    )
    for subscription in subscriptions:
        await session.delete(subscription)
    await session.commit()

    await resolve_all_subscriptions(provider=_provider(request), session=session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/queue",
    response_model=QueueResponse,
    dependencies=[Depends(require_ui_auth)],
)
async def get_queue(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[
        list[Literal["queued", "transferring", "ready", "failed"]] | None,
        Query(alias="status"),
    ] = None,
    client_id: str | None = None,
) -> QueueResponse:
    settings = get_settings()
    now = datetime.now(UTC)

    query = (
        select(Assignment, Asset, Client)
        .join(Asset, Assignment.asset_id == Asset.id)
        .join(Client, Assignment.client_id == Client.id)
        .order_by(Assignment.created_at.desc())
    )
    if client_id is not None:
        query = query.where(Assignment.client_id == client_id)

    result = await session.execute(query)

    rows: list[QueueRowSchema] = []
    for assignment, asset, client in result.all():
        samples = rate_tracker.samples_for((client.id, asset.id))
        p = project(
            asset,
            assignment,
            client,
            now=now,
            poll_interval_seconds=settings.agent_poll_interval_seconds,
            rate_samples=samples,
        )
        if not p.visible:
            continue
        if status is not None and p.status not in status:
            continue
        rows.append(
            QueueRowSchema(
                asset_id=asset.id,
                client_id=client.id,
                media_item_id=asset.source_media_id,
                filename=Path(asset.source_path).name,
                profile_id=asset.profile_id,
                size_bytes=p.size_bytes,
                bytes_downloaded=p.bytes_downloaded,
                transfer_rate_bps=p.transfer_rate_bps,
                eta_seconds=p.eta_seconds,
                pipeline_status=p.status or "queued",
                pipeline_substate=p.substate,
                pipeline_detail=p.detail,
                delivered_at=assignment.delivered_at,
                created_at=assignment.created_at,
            )
        )

    rows.sort(key=lambda row: _PIPELINE_SORT_ORDER.get(row.pipeline_status, 99))
    return QueueResponse(rows=rows)


@router.get(
    "/assets",
    response_model=list[AssetStatusSchema],
    dependencies=[Depends(require_ui_auth)],
)
async def list_assets(
    session: Annotated[AsyncSession, Depends(get_session)],
    media_item_ids: str = "",
    status: str = "",
) -> list[AssetStatusSchema]:
    from sqlalchemy import func

    ids = [id_.strip() for id_ in media_item_ids.split(",") if id_.strip()]
    progress_sq = (
        select(
            Assignment.asset_id,
            func.max(Assignment.bytes_downloaded).label("bytes_downloaded"),
        )
        .group_by(Assignment.asset_id)
        .subquery()
    )

    query = (
        select(Asset, progress_sq.c.bytes_downloaded)
        .outerjoin(progress_sq, Asset.id == progress_sq.c.asset_id)
        .order_by(Asset.created_at.desc())
    )
    if ids:
        query = query.where(Asset.source_media_id.in_(ids))
    if status:
        query = query.where(Asset.status == status)
    rows = list((await session.execute(query)).all())
    return [
        AssetStatusSchema(
            asset_id=asset.id,
            media_item_id=asset.source_media_id,
            profile_id=asset.profile_id,
            filename=Path(asset.source_path).name,
            status=asset.status,
            status_detail=asset.status_detail,
            size_bytes=asset.size_bytes,
            ready_at=asset.ready_at,
            bytes_downloaded=bytes_downloaded,
        )
        for asset, bytes_downloaded in rows
    ]
