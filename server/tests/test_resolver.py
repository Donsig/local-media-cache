from __future__ import annotations

from typing import cast

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.main import app
from syncarr_server.models import Asset, Assignment
from syncarr_server.providers.base import MediaProvider
from syncarr_server.resolver import resolve_all_subscriptions

pytestmark = pytest.mark.asyncio


async def _create_client(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    client_id: str,
) -> None:
    response = await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": client_id, "name": f"Client {client_id}"},
    )
    assert response.status_code == 201


async def _create_profile(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    profile_id: str = "p1",
) -> None:
    response = await http_client.post(
        "/profiles",
        headers=auth_headers_ui,
        json={
            "id": profile_id,
            "name": f"Profile {profile_id}",
            "ffmpeg_args": ["-c:v", "libx265"],
            "target_size_bytes": 1_000_000_000,
        },
    )
    assert response.status_code == 201


async def _create_subscription(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    *,
    client_id: str,
    media_item_id: str,
    scope_type: str,
    profile_id: str = "p1",
    scope_params: dict[str, object] | None = None,
) -> int:
    response = await http_client.post(
        "/subscriptions",
        headers=auth_headers_ui,
        json={
            "client_id": client_id,
            "media_item_id": media_item_id,
            "scope_type": scope_type,
            "scope_params": scope_params,
            "profile_id": profile_id,
        },
    )
    assert response.status_code == 201
    return int(response.json()["id"])


async def test_new_subscription_creates_assets(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await _create_client(http_client, auth_headers_ui, "client-a")
    await _create_profile(http_client, auth_headers_ui)
    await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="client-a",
        media_item_id="s1",
        scope_type="show:all",
    )

    asset_result = await db_session.execute(select(Asset).order_by(Asset.source_media_id))
    assets = list(asset_result.scalars())

    assert [asset.source_media_id for asset in assets] == ["e1", "e2"]
    assert all(asset.status == "queued" for asset in assets)


async def test_new_subscription_creates_assignments(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await _create_client(http_client, auth_headers_ui, "client-a")
    await _create_profile(http_client, auth_headers_ui)
    await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="client-a",
        media_item_id="s1",
        scope_type="show:all",
    )

    assignments = list(
        (await db_session.execute(select(Assignment).order_by(Assignment.asset_id))).scalars(),
    )

    assert len(assignments) == 2
    assert all(assignment.client_id == "client-a" for assignment in assignments)
    assert all(assignment.state == "pending" for assignment in assignments)


async def test_resolve_is_idempotent(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await _create_client(http_client, auth_headers_ui, "client-a")
    await _create_profile(http_client, auth_headers_ui)
    await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="client-a",
        media_item_id="s1",
        scope_type="show:all",
    )

    assets_before = (await db_session.execute(select(func.count()).select_from(Asset))).scalar_one()
    assignments_before = (
        await db_session.execute(select(func.count()).select_from(Assignment))
    ).scalar_one()
    await db_session.commit()

    provider = cast(MediaProvider, app.state.media_provider)
    await resolve_all_subscriptions(provider=provider, session=db_session)

    assets_after = (await db_session.execute(select(func.count()).select_from(Asset))).scalar_one()
    assignments_after = (
        await db_session.execute(select(func.count()).select_from(Assignment))
    ).scalar_one()

    assert assets_after == assets_before
    assert assignments_after == assignments_before


async def test_remove_subscription_evicts_assignments(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await _create_client(http_client, auth_headers_ui, "client-a")
    await _create_profile(http_client, auth_headers_ui)
    subscription_id = await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="client-a",
        media_item_id="s1",
        scope_type="show:all",
    )

    response = await http_client.delete(
        f"/subscriptions/{subscription_id}",
        headers=auth_headers_ui,
    )
    assignments = list((await db_session.execute(select(Assignment))).scalars())

    assert response.status_code == 204
    assert len(assignments) == 2
    assert all(assignment.state == "evict" for assignment in assignments)
    assert all(assignment.evict_requested_at is not None for assignment in assignments)


async def test_resubscribe_mid_eviction_cancels_evict(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await _create_client(http_client, auth_headers_ui, "client-a")
    await _create_profile(http_client, auth_headers_ui)
    subscription_id = await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="client-a",
        media_item_id="s1",
        scope_type="show:all",
    )

    delete_response = await http_client.delete(
        f"/subscriptions/{subscription_id}",
        headers=auth_headers_ui,
    )
    assert delete_response.status_code == 204

    asset_ids_before = {
        assignment.asset_id
        for assignment in (await db_session.execute(select(Assignment))).scalars()
    }

    await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="client-a",
        media_item_id="s1",
        scope_type="show:all",
    )

    assignments = list((await db_session.execute(select(Assignment))).scalars())

    assert len(assignments) == 2
    assert {assignment.asset_id for assignment in assignments} == asset_ids_before
    assert all(assignment.state == "pending" for assignment in assignments)
    assert all(assignment.evict_requested_at is None for assignment in assignments)


async def test_deduplication_across_subscriptions(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await _create_client(http_client, auth_headers_ui, "client-a")
    await _create_client(http_client, auth_headers_ui, "client-b")
    await _create_profile(http_client, auth_headers_ui)
    await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="client-a",
        media_item_id="m1",
        scope_type="movie",
    )
    await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="client-b",
        media_item_id="m1",
        scope_type="movie",
    )

    assets = list((await db_session.execute(select(Asset))).scalars())
    assignments = list(
        (await db_session.execute(select(Assignment).order_by(Assignment.client_id))).scalars(),
    )

    assert len(assets) == 1
    assert assets[0].source_media_id == "m1"
    assert len(assignments) == 2
    assert [assignment.client_id for assignment in assignments] == ["client-a", "client-b"]
