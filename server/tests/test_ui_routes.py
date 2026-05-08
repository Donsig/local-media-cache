from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.models import Asset, Assignment, Client, Profile

pytestmark = pytest.mark.asyncio


async def _create_client(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    client_id: str,
    name: str | None = None,
) -> None:
    response = await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": client_id, "name": name or client_id.title()},
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
        json={"id": profile_id, "name": "1080p H265", "ffmpeg_args": ["-c:v", "libx265"]},
    )
    assert response.status_code == 201


async def _create_subscription(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    *,
    client_id: str,
    media_item_id: str,
    scope_type: str = "movie",
    scope_params: dict[str, object] | None = None,
    profile_id: str = "p1",
) -> None:
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


async def test_get_assets(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    profile = Profile(
        id="p-test",
        name="Test Profile",
        ffmpeg_args=None,
        target_size_bytes=None,
        created_at=datetime.now(UTC),
    )
    db_session.add(profile)
    await db_session.flush()

    asset = Asset(
        source_media_id="ep-42",
        profile_id="p-test",
        source_path="/mnt/media/ep42.mkv",
        cache_path=None,
        size_bytes=None,
        sha256=None,
        status="queued",
        status_detail=None,
        created_at=datetime.now(UTC),
        ready_at=None,
    )
    db_session.add(asset)
    await db_session.commit()

    response = await http_client.get(
        "/assets?media_item_ids=ep-42",
        headers=auth_headers_ui,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["asset_id"] == asset.id
    assert data[0]["media_item_id"] == "ep-42"
    assert data[0]["filename"] == "ep42.mkv"
    assert data[0]["status"] == "queued"
    assert data[0]["profile_id"] == "p-test"


async def test_list_client_assignments(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await _create_client(http_client, auth_headers_ui, "caravan", "Caravan")
    await _create_client(http_client, auth_headers_ui, "rv", "RV")
    await _create_profile(http_client, auth_headers_ui)
    await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="caravan",
        media_item_id="m1",
    )

    asset = Asset(
        source_media_id="ep-ready",
        profile_id="p1",
        source_path="/mnt/media/ep-ready.mkv",
        cache_path="/mnt/cache/ep-ready.mkv",
        size_bytes=1234,
        sha256="abc123",
        status="ready",
        status_detail=None,
        created_at=datetime.now(UTC),
        ready_at=datetime.now(UTC),
    )
    db_session.add(asset)
    await db_session.flush()

    assignment = Assignment(
        client_id="caravan",
        asset_id=asset.id,
        state="pending",
        created_at=datetime.now(UTC),
        delivered_at=None,
        evict_requested_at=None,
    )
    db_session.add(assignment)
    await db_session.commit()

    response = await http_client.get(
        "/clients/caravan/assignments?media_item_ids=ep-ready",
        headers=auth_headers_ui,
    )
    other_client_response = await http_client.get(
        "/clients/rv/assignments?media_item_ids=ep-ready",
        headers=auth_headers_ui,
    )

    assert response.status_code == 200
    assert response.json() == [{"media_item_id": "ep-ready", "state": "queued"}]

    assert other_client_response.status_code == 200
    assert other_client_response.json() == []

    # Also test: delivered assignment -> appears as "ready" in UI view (Bug #16 fix)
    asset2 = Asset(
        source_media_id="ep-delivered",
        profile_id="p1",
        source_path="/mnt/media/ep-delivered.mkv",
        cache_path=None,
        size_bytes=5678,
        sha256="def456",
        status="ready",
        status_detail=None,
        created_at=datetime.now(UTC),
        ready_at=datetime.now(UTC),
    )
    db_session.add(asset2)
    await db_session.flush()

    assignment2 = Assignment(
        client_id="caravan",
        asset_id=asset2.id,
        state="delivered",
        created_at=datetime.now(UTC),
        delivered_at=datetime.now(UTC),
        evict_requested_at=None,
    )
    db_session.add(assignment2)
    await db_session.commit()

    delivered_response = await http_client.get(
        "/clients/caravan/assignments?media_item_ids=ep-delivered",
        headers=auth_headers_ui,
    )
    assert delivered_response.status_code == 200
    assert delivered_response.json() == [
        {"media_item_id": "ep-delivered", "state": "ready"}
    ]


async def test_list_client_assignments_ready_asset_shows_queued(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
    agent_client: Client,
) -> None:
    """An asset ready on server but not yet delivered to satellite shows as 'queued'."""
    profile = Profile(
        id="p-pill28",
        name="Pill28",
        ffmpeg_args=None,
        target_size_bytes=None,
        created_at=datetime.now(UTC),
    )
    db_session.add(profile)
    await db_session.flush()

    asset = Asset(
        source_media_id="ep-ready-not-delivered",
        profile_id="p-pill28",
        source_path="/mnt/media/ep.mkv",
        cache_path=None,
        size_bytes=1234,
        sha256="abc123",
        status="ready",
        status_detail=None,
        created_at=datetime.now(UTC),
        ready_at=datetime.now(UTC),
    )
    db_session.add(asset)
    await db_session.flush()

    db_session.add(
        Assignment(
            client_id=agent_client.id,
            asset_id=asset.id,
            state="pending",
            created_at=datetime.now(UTC),
            delivered_at=None,
            evict_requested_at=None,
        )
    )
    await db_session.commit()

    response = await http_client.get(
        f"/clients/{agent_client.id}/assignments?media_item_ids=ep-ready-not-delivered",
        headers=auth_headers_ui,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["state"] == "queued"


async def test_list_all_assets_no_filter(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    profile = Profile(
        id="p-all",
        name="All Assets Profile",
        ffmpeg_args=None,
        target_size_bytes=None,
        created_at=datetime.now(UTC),
    )
    db_session.add(profile)
    await db_session.flush()

    asset1 = Asset(
        source_media_id="ep-older",
        profile_id="p-all",
        source_path="/mnt/media/older.mkv",
        cache_path=None,
        size_bytes=None,
        sha256=None,
        status="queued",
        status_detail=None,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        ready_at=None,
    )
    asset2 = Asset(
        source_media_id="ep-newer",
        profile_id="p-all",
        source_path="/mnt/media/newer.mkv",
        cache_path=None,
        size_bytes=None,
        sha256=None,
        status="ready",
        status_detail=None,
        created_at=datetime(2025, 6, 1, tzinfo=UTC),
        ready_at=None,
    )
    db_session.add(asset1)
    db_session.add(asset2)
    await db_session.commit()

    response = await http_client.get("/assets", headers=auth_headers_ui)

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    # Ordered by created_at DESC — newer first
    assert data[0]["media_item_id"] == "ep-newer"
    assert data[0]["status"] == "ready"
    assert data[1]["media_item_id"] == "ep-older"
    assert data[1]["status"] == "queued"


async def test_get_assets_includes_bytes_downloaded(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
    agent_client: Client,
) -> None:
    """GET /assets includes bytes_downloaded from assignments."""
    profile = Profile(
        id="p-progress",
        name="Progress",
        ffmpeg_args=None,
        target_size_bytes=None,
        created_at=datetime.now(UTC),
    )
    db_session.add(profile)
    await db_session.flush()

    asset = Asset(
        source_media_id="ep-progress",
        profile_id="p-progress",
        source_path="/mnt/media/ep.mkv",
        cache_path=None,
        size_bytes=100_000,
        sha256=None,
        status="ready",
        status_detail=None,
        created_at=datetime.now(UTC),
        ready_at=datetime.now(UTC),
    )
    db_session.add(asset)
    await db_session.flush()

    db_session.add(
        Assignment(
            client_id=agent_client.id,
            asset_id=asset.id,
            state="pending",
            created_at=datetime.now(UTC),
            delivered_at=None,
            evict_requested_at=None,
            bytes_downloaded=42_000,
        )
    )
    await db_session.commit()

    response = await http_client.get(
        "/assets?media_item_ids=ep-progress",
        headers=auth_headers_ui,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["bytes_downloaded"] == 42_000


async def test_list_client_assignments_no_filter_returns_all(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /clients/{id}/assignments without media_item_ids returns all assignments (bug #36)."""
    await _create_client(http_client, auth_headers_ui, "c-nofilter")
    await _create_profile(http_client, auth_headers_ui, "p-nofilter")

    profile = await db_session.get(Profile, "p-nofilter")
    assert profile is not None

    for i in range(3):
        asset = Asset(
            source_media_id=f"ep-nf-{i}",
            profile_id="p-nofilter",
            source_path=f"/mnt/media/ep{i}.mkv",
            cache_path=None,
            size_bytes=None,
            sha256=None,
            status="queued",
            status_detail=None,
            created_at=datetime.now(UTC),
            ready_at=None,
        )
        db_session.add(asset)
        await db_session.flush()
        db_session.add(
            Assignment(
                client_id="c-nofilter",
                asset_id=asset.id,
                state="pending",
                created_at=datetime.now(UTC),
                delivered_at=None,
                evict_requested_at=None,
            )
        )
    await db_session.commit()

    response = await http_client.get(
        "/clients/c-nofilter/assignments",
        headers=auth_headers_ui,
    )

    assert response.status_code == 200
    assert len(response.json()) == 3


async def test_list_assets_status_filter(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET /assets?status=ready filters by status (bug #41)."""
    profile = Profile(
        id="p-statusfilter",
        name="Status Filter",
        ffmpeg_args=None,
        target_size_bytes=None,
        created_at=datetime.now(UTC),
    )
    db_session.add(profile)
    await db_session.flush()

    for status_val in ("ready", "queued", "failed"):
        db_session.add(
            Asset(
                source_media_id=f"ep-sf-{status_val}",
                profile_id="p-statusfilter",
                source_path=f"/mnt/media/{status_val}.mkv",
                cache_path=None,
                size_bytes=None,
                sha256=None,
                status=status_val,
                status_detail=None,
                created_at=datetime.now(UTC),
                ready_at=None,
            )
        )
    await db_session.commit()

    response = await http_client.get("/assets?status=ready", headers=auth_headers_ui)

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "ready"
