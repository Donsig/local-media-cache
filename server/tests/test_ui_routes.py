from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.models import Asset, Assignment, Client

pytestmark = pytest.mark.asyncio


async def test_create_client(http_client: AsyncClient, auth_headers_ui: dict[str, str]) -> None:
    response = await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": "caravan", "name": "Caravan"},
    )

    assert response.status_code == 201
    assert response.json()["id"] == "caravan"
    assert response.json()["name"] == "Caravan"
    assert response.json()["auth_token"].startswith("agent-caravan-")


async def test_create_profile(http_client: AsyncClient, auth_headers_ui: dict[str, str]) -> None:
    response = await http_client.post(
        "/profiles",
        headers=auth_headers_ui,
        json={
            "id": "p1",
            "name": "1080p H265",
            "ffmpeg_args": ["-c:v", "libx265"],
            "target_size_bytes": 1_500_000_000,
        },
    )

    assert response.status_code == 201
    assert response.json()["id"] == "p1"
    assert response.json()["name"] == "1080p H265"


async def test_create_subscription_triggers_resolve(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    client_response = await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": "caravan", "name": "Caravan"},
    )
    assert client_response.status_code == 201

    profile_response = await http_client.post(
        "/profiles",
        headers=auth_headers_ui,
        json={
            "id": "p1",
            "name": "1080p H265",
            "ffmpeg_args": ["-c:v", "libx265"],
            "target_size_bytes": 1_500_000_000,
        },
    )
    assert profile_response.status_code == 201

    response = await http_client.post(
        "/subscriptions",
        headers=auth_headers_ui,
        json={
            "client_id": "caravan",
            "media_item_id": "s1",
            "scope_type": "show:all",
            "scope_params": None,
            "profile_id": "p1",
        },
    )

    asset_result = await db_session.execute(select(Asset).order_by(Asset.source_media_id))
    assets = list(asset_result.scalars())

    assert response.status_code == 201
    assert [asset.status for asset in assets] == ["queued", "queued"]


async def test_delete_subscription_triggers_eviction(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": "caravan", "name": "Caravan"},
    )
    await http_client.post(
        "/profiles",
        headers=auth_headers_ui,
        json={"id": "p1", "name": "1080p H265", "ffmpeg_args": ["-c:v", "libx265"]},
    )
    subscription_response = await http_client.post(
        "/subscriptions",
        headers=auth_headers_ui,
        json={
            "client_id": "caravan",
            "media_item_id": "s1",
            "scope_type": "show:all",
            "scope_params": None,
            "profile_id": "p1",
        },
    )
    subscription_id = subscription_response.json()["id"]

    response = await http_client.delete(
        f"/subscriptions/{subscription_id}",
        headers=auth_headers_ui,
    )

    assignments = list((await db_session.execute(select(Assignment))).scalars())

    assert response.status_code == 204
    assert len(assignments) == 2
    assert all(assignment.state == "evict" for assignment in assignments)


async def test_subscription_scope_validation(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": "caravan", "name": "Caravan"},
    )
    await http_client.post(
        "/profiles",
        headers=auth_headers_ui,
        json={"id": "p1", "name": "1080p H265", "ffmpeg_args": ["-c:v", "libx265"]},
    )

    response = await http_client.post(
        "/subscriptions",
        headers=auth_headers_ui,
        json={
            "client_id": "caravan",
            "media_item_id": "s1",
            "scope_type": "show:not-real",
            "scope_params": None,
            "profile_id": "p1",
        },
    )

    assert response.status_code == 422


async def test_decommission_client(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": "caravan", "name": "Caravan"},
    )
    await http_client.post(
        "/profiles",
        headers=auth_headers_ui,
        json={"id": "p1", "name": "1080p H265", "ffmpeg_args": ["-c:v", "libx265"]},
    )
    await http_client.post(
        "/subscriptions",
        headers=auth_headers_ui,
        json={
            "client_id": "caravan",
            "media_item_id": "s1",
            "scope_type": "show:all",
            "scope_params": None,
            "profile_id": "p1",
        },
    )

    response = await http_client.delete("/clients/caravan", headers=auth_headers_ui)

    client = await db_session.get(Client, "caravan")
    assignments = list((await db_session.execute(select(Assignment))).scalars())

    assert response.status_code == 202
    assert client is not None
    assert client.decommissioning is True
    assert len(assignments) == 2
    assert all(assignment.state == "evict" for assignment in assignments)
