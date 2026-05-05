from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.models import Asset, Assignment, Client, Profile

from .conftest import AgentTestFiles

pytestmark = pytest.mark.asyncio


async def _ensure_profile(session: AsyncSession) -> None:
    profile = await session.get(Profile, "profile-1")
    if profile is not None:
        return

    session.add(
        Profile(
            id="profile-1",
            name="Profile 1",
            ffmpeg_args=["-c:v", "libx265"],
            target_size_bytes=None,
            created_at=datetime.now(UTC),
        ),
    )
    await session.flush()


async def _create_asset_assignment(
    session: AsyncSession,
    *,
    client_id: str,
    files: AgentTestFiles,
    source_media_id: str = "media-1",
    asset_status: str = "ready",
    assignment_state: str = "pending",
    passthrough: bool = False,
) -> int:
    await _ensure_profile(session)

    cache_path: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    if asset_status == "ready":
        if passthrough:
            size_bytes = files.source_size_bytes
            sha256 = files.source_sha256
        else:
            cache_path = str(files.cache_path)
            size_bytes = files.cache_size_bytes
            sha256 = files.cache_sha256

    now = datetime.now(UTC)
    asset = Asset(
        source_media_id=source_media_id,
        profile_id="profile-1",
        source_path=str(files.source_path),
        cache_path=cache_path,
        size_bytes=size_bytes,
        sha256=sha256,
        status=asset_status,
        status_detail=None,
        created_at=now,
        ready_at=now if asset_status == "ready" else None,
    )
    session.add(asset)
    await session.flush()
    session.add(
        Assignment(
            client_id=client_id,
            asset_id=asset.id,
            state=assignment_state,
            created_at=now,
            delivered_at=now if assignment_state == "delivered" else None,
            evict_requested_at=now if assignment_state == "evict" else None,
        ),
    )
    await session.commit()
    return asset.id


async def _get_assignment(
    session: AsyncSession,
    *,
    client_id: str,
    asset_id: int,
) -> Assignment | None:
    result = await session.execute(
        select(Assignment).where(
            Assignment.client_id == client_id,
            Assignment.asset_id == asset_id,
        ),
    )
    return result.scalar_one_or_none()


async def test_assignments_returns_ready_state(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    client_id = agent_client.id
    asset_id = await _create_asset_assignment(
        db_session,
        client_id=client_id,
        files=agent_test_files,
    )

    response = await http_client.get("/assignments", headers=auth_headers_agent)

    assert response.status_code == 200
    assignment = response.json()["assignments"][0]
    assert assignment["asset_id"] == asset_id
    assert assignment["state"] == "ready"
    assert assignment["sha256"] == agent_test_files.cache_sha256
    assert assignment["download_url"] == f"/download/{asset_id}"


async def test_assignments_returns_queued_state(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    await _create_asset_assignment(
        db_session,
        client_id=agent_client.id,
        files=agent_test_files,
        asset_status="queued",
    )

    response = await http_client.get("/assignments", headers=auth_headers_agent)

    assignment = response.json()["assignments"][0]
    assert assignment["state"] == "queued"
    assert "sha256" not in assignment
    assert "download_url" not in assignment


async def test_assignments_returns_queued_for_transcoding(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    await _create_asset_assignment(
        db_session,
        client_id=agent_client.id,
        files=agent_test_files,
        asset_status="transcoding",
    )

    response = await http_client.get("/assignments", headers=auth_headers_agent)

    assert response.json()["assignments"][0]["state"] == "queued"


async def test_assignments_returns_queued_for_failed(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    await _create_asset_assignment(
        db_session,
        client_id=agent_client.id,
        files=agent_test_files,
        asset_status="failed",
    )

    response = await http_client.get("/assignments", headers=auth_headers_agent)

    assert response.json()["assignments"][0]["state"] == "queued"


async def test_assignments_omits_delivered(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    await _create_asset_assignment(
        db_session,
        client_id=agent_client.id,
        files=agent_test_files,
        assignment_state="delivered",
    )

    response = await http_client.get("/assignments", headers=auth_headers_agent)

    assert response.json()["assignments"] == []


async def test_assignments_returns_evict(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    await _create_asset_assignment(
        db_session,
        client_id=agent_client.id,
        files=agent_test_files,
        asset_status="queued",
        assignment_state="evict",
    )

    response = await http_client.get("/assignments", headers=auth_headers_agent)

    assert response.json()["assignments"][0]["state"] == "evict"


async def test_assignments_evict_overrides_ready(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    await _create_asset_assignment(
        db_session,
        client_id=agent_client.id,
        files=agent_test_files,
        assignment_state="evict",
    )

    response = await http_client.get("/assignments", headers=auth_headers_agent)

    assignment = response.json()["assignments"][0]
    assert assignment["state"] == "evict"
    assert "sha256" not in assignment
    assert "download_url" not in assignment


async def test_assignments_updates_last_seen(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    db_session: AsyncSession,
) -> None:
    client_id = agent_client.id
    assert agent_client.last_seen is None

    response = await http_client.get("/assignments", headers=auth_headers_agent)
    db_session.expire_all()
    client = await db_session.get(Client, client_id)

    assert response.status_code == 200
    assert client is not None
    assert client.last_seen is not None


async def test_assignments_agent_scoped(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_client_b: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    asset_a = await _create_asset_assignment(
        db_session,
        client_id=agent_client.id,
        files=agent_test_files,
        source_media_id="media-a",
    )
    asset_b = await _create_asset_assignment(
        db_session,
        client_id=agent_client_b.id,
        files=agent_test_files,
        source_media_id="media-b",
    )

    response = await http_client.get("/assignments", headers=auth_headers_agent)

    asset_ids = {assignment["asset_id"] for assignment in response.json()["assignments"]}
    assert asset_ids == {asset_a}
    assert asset_b not in asset_ids


async def test_download_returns_file(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    client_id = agent_client.id
    asset_id = await _create_asset_assignment(
        db_session,
        client_id=client_id,
        files=agent_test_files,
    )

    response = await http_client.get(f"/download/{asset_id}", headers=auth_headers_agent)

    assert response.status_code == 200
    assert response.content == Path(agent_test_files.cache_path).read_bytes()


async def test_download_passthrough_serves_source_path(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    asset_id = await _create_asset_assignment(
        db_session,
        client_id=agent_client.id,
        files=agent_test_files,
        passthrough=True,
    )

    response = await http_client.get(f"/download/{asset_id}", headers=auth_headers_agent)

    assert response.status_code == 200
    assert response.content == Path(agent_test_files.source_path).read_bytes()


async def test_download_supports_range(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    asset_id = await _create_asset_assignment(
        db_session,
        client_id=agent_client.id,
        files=agent_test_files,
    )

    response = await http_client.get(
        f"/download/{asset_id}",
        headers={**auth_headers_agent, "Range": "bytes=0-1023"},
    )

    assert response.status_code == 206
    assert response.headers["content-range"] == (
        f"bytes 0-1023/{agent_test_files.cache_size_bytes}"
    )
    assert len(response.content) == 1024
    assert response.content == Path(agent_test_files.cache_path).read_bytes()[:1024]


async def test_download_404_for_non_ready(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    asset_id = await _create_asset_assignment(
        db_session,
        client_id=agent_client.id,
        files=agent_test_files,
        asset_status="queued",
    )

    response = await http_client.get(f"/download/{asset_id}", headers=auth_headers_agent)

    assert response.status_code == 404


async def test_confirm_delivered_success(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    client_id = agent_client.id
    asset_id = await _create_asset_assignment(
        db_session,
        client_id=client_id,
        files=agent_test_files,
    )

    response = await http_client.post(
        f"/confirm/{asset_id}",
        headers=auth_headers_agent,
        json={
            "state": "delivered",
            "actual_sha256": agent_test_files.cache_sha256,
            "actual_size_bytes": agent_test_files.cache_size_bytes,
        },
    )
    db_session.expire_all()
    assignment = await _get_assignment(db_session, client_id=client_id, asset_id=asset_id)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert assignment is not None
    assert assignment.state == "delivered"
    assert assignment.delivered_at is not None


async def test_confirm_delivered_sha256_mismatch(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    client_id = agent_client.id
    asset_id = await _create_asset_assignment(
        db_session,
        client_id=client_id,
        files=agent_test_files,
    )

    response = await http_client.post(
        f"/confirm/{asset_id}",
        headers=auth_headers_agent,
        json={
            "state": "delivered",
            "actual_sha256": "bad-sha256",
            "actual_size_bytes": agent_test_files.cache_size_bytes,
        },
    )
    db_session.expire_all()
    assignment = await _get_assignment(db_session, client_id=client_id, asset_id=asset_id)

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "reason": "checksum_mismatch",
        "expected_sha256": agent_test_files.cache_sha256,
        "actual_sha256": "bad-sha256",
    }
    assert assignment is not None
    assert assignment.state == "pending"


async def test_confirm_evicted(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    client_id = agent_client.id
    asset_id = await _create_asset_assignment(
        db_session,
        client_id=client_id,
        files=agent_test_files,
        assignment_state="evict",
    )

    response = await http_client.post(
        f"/confirm/{asset_id}",
        headers=auth_headers_agent,
        json={"state": "evicted"},
    )
    db_session.expire_all()
    assignment = await _get_assignment(db_session, client_id=client_id, asset_id=asset_id)
    asset = await db_session.get(Asset, asset_id)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert assignment is None
    assert asset is None


async def test_confirm_is_idempotent(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    client_id = agent_client.id
    asset_id = await _create_asset_assignment(
        db_session,
        client_id=client_id,
        files=agent_test_files,
        assignment_state="delivered",
    )

    response = await http_client.post(
        f"/confirm/{asset_id}",
        headers=auth_headers_agent,
        json={
            "state": "delivered",
            "actual_sha256": "already-delivered-does-not-recheck",
            "actual_size_bytes": 1,
        },
    )
    db_session.expire_all()
    assignment = await _get_assignment(db_session, client_id=client_id, asset_id=asset_id)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert assignment is not None
    assert assignment.state == "delivered"
