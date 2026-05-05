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

# ---------------------------------------------------------------------------
# Helpers — mirror the pattern used in test_agent_routes.py and test_resolver.py
# ---------------------------------------------------------------------------


async def _create_client(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    client_id: str,
) -> str:
    response = await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": client_id, "name": f"Client {client_id}"},
    )
    assert response.status_code == 201
    return client_id


async def _create_profile(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    profile_id: str = "p1",
) -> str:
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
    return profile_id


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


async def _ensure_profile(session: AsyncSession, profile_id: str = "profile-1") -> None:
    profile = await session.get(Profile, profile_id)
    if profile is not None:
        return
    session.add(
        Profile(
            id=profile_id,
            name=f"Profile {profile_id}",
            ffmpeg_args=["-c:v", "libx265"],
            target_size_bytes=None,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _add_assignment(
    session: AsyncSession,
    *,
    client: Client,
    files: AgentTestFiles,
    source_media_id: str = "media-1",
    asset_status: str = "ready",
    assignment_state: str = "evict",
    cache_path: str | None = None,
    passthrough: bool = False,
) -> tuple[int, Asset]:
    """Create an asset+assignment pair directly in the DB (bypasses HTTP layer)."""
    await _ensure_profile(session)

    effective_cache_path = cache_path
    size_bytes: int | None = None
    sha256: str | None = None

    if asset_status == "ready":
        if passthrough:
            size_bytes = files.source_size_bytes
            sha256 = files.source_sha256
        else:
            effective_cache_path = cache_path if cache_path is not None else str(files.cache_path)
            size_bytes = files.cache_size_bytes
            sha256 = files.cache_sha256

    now = datetime.now(UTC)
    asset = Asset(
        source_media_id=source_media_id,
        profile_id="profile-1",
        source_path=str(files.source_path),
        cache_path=effective_cache_path,
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
            client_id=client.id,
            asset_id=asset.id,
            state=assignment_state,
            created_at=now,
            delivered_at=now if assignment_state == "delivered" else None,
            evict_requested_at=now if assignment_state == "evict" else None,
        )
    )
    await session.commit()
    return asset.id, asset


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_delete_subscription_evicts_assignments(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """DELETE /subscriptions/{id} → resolver flips assignments to evict."""
    await _create_client(http_client, auth_headers_ui, "caravan")
    await _create_profile(http_client, auth_headers_ui)
    sub_id = await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="caravan",
        media_item_id="s1",
        scope_type="show:all",
    )

    response = await http_client.delete(f"/subscriptions/{sub_id}", headers=auth_headers_ui)

    assignments = list((await db_session.execute(select(Assignment))).scalars())
    assert response.status_code == 204
    assert len(assignments) == 2
    assert all(a.state == "evict" for a in assignments)


async def test_confirm_eviction_deletes_assignment(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    """Assignment in evict → POST /confirm evicted → assignment row deleted."""
    asset_id, _ = await _add_assignment(
        db_session,
        client=agent_client,
        files=agent_test_files,
        assignment_state="evict",
    )

    client_id = agent_client.id  # capture before expire_all invalidates the object

    response = await http_client.post(
        f"/confirm/{asset_id}",
        headers=auth_headers_agent,
        json={"state": "evicted"},
    )

    db_session.expire_all()
    assignment = (
        await db_session.execute(
            select(Assignment).where(
                Assignment.client_id == client_id,
                Assignment.asset_id == asset_id,
            )
        )
    ).scalar_one_or_none()

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert assignment is None


async def test_gc_deletes_asset_after_last_eviction(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    auth_headers_agent_b: dict[str, str],
    agent_client: Client,
    agent_client_b: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    """Two clients share one asset. First evicts → asset survives. Second evicts → asset gone."""
    now = datetime.now(UTC)
    await _ensure_profile(db_session)

    # Shared asset
    asset = Asset(
        source_media_id="shared-media",
        profile_id="profile-1",
        source_path=str(agent_test_files.source_path),
        cache_path=str(agent_test_files.cache_path),
        size_bytes=agent_test_files.cache_size_bytes,
        sha256=agent_test_files.cache_sha256,
        status="ready",
        status_detail=None,
        created_at=now,
        ready_at=now,
    )
    db_session.add(asset)
    await db_session.flush()
    asset_id = asset.id

    db_session.add(
        Assignment(
            client_id=agent_client.id,
            asset_id=asset_id,
            state="evict",
            created_at=now,
            delivered_at=None,
            evict_requested_at=now,
        )
    )
    db_session.add(
        Assignment(
            client_id=agent_client_b.id,
            asset_id=asset_id,
            state="evict",
            created_at=now,
            delivered_at=None,
            evict_requested_at=now,
        )
    )
    await db_session.commit()

    # First client evicts: asset row should still exist (second assignment remains)
    response_a = await http_client.post(
        f"/confirm/{asset_id}",
        headers=auth_headers_agent,
        json={"state": "evicted"},
    )
    assert response_a.status_code == 200

    db_session.expire_all()
    asset_after_first = await db_session.get(Asset, asset_id)
    assert asset_after_first is not None, "Asset must survive while second assignment exists"

    # Second client evicts: asset row should be gone
    response_b = await http_client.post(
        f"/confirm/{asset_id}",
        headers=auth_headers_agent_b,
        json={"state": "evicted"},
    )
    assert response_b.status_code == 200

    db_session.expire_all()
    asset_after_second = await db_session.get(Asset, asset_id)
    assert asset_after_second is None, "Asset must be deleted once all assignments are evicted"


async def test_gc_deletes_cache_file(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """GC deletes the on-disk cache file when the last assignment is evicted."""
    cache_file = tmp_path / "asset_gc_test.mkv"
    cache_file.write_bytes(b"fake-transcoded-content")

    asset_id, _ = await _add_assignment(
        db_session,
        client=agent_client,
        files=agent_test_files,
        assignment_state="evict",
        cache_path=str(cache_file),
    )

    assert cache_file.exists(), "Pre-condition: file must exist before eviction"

    response = await http_client.post(
        f"/confirm/{asset_id}",
        headers=auth_headers_agent,
        json={"state": "evicted"},
    )

    assert response.status_code == 200
    assert not cache_file.exists(), "Cache file must be deleted after last eviction is confirmed"


async def test_gc_skips_passthrough_asset_file_deletion(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    """Passthrough asset (cache_path=None) → GC deletes row, does not attempt file deletion."""
    asset_id, _ = await _add_assignment(
        db_session,
        client=agent_client,
        files=agent_test_files,
        assignment_state="evict",
        passthrough=True,
        cache_path=None,
    )

    source_file = Path(agent_test_files.source_path)
    assert source_file.exists(), "Pre-condition: source file must exist"

    response = await http_client.post(
        f"/confirm/{asset_id}",
        headers=auth_headers_agent,
        json={"state": "evicted"},
    )

    assert response.status_code == 200

    db_session.expire_all()
    asset_row = await db_session.get(Asset, asset_id)
    assert asset_row is None, "Asset row must be deleted even for passthrough"

    # Source file on NFS must not be touched
    assert source_file.exists(), "Source (NFS) file must not be deleted"


async def test_decommission_client_cascades(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """DELETE /clients/{id} → decommissioning=True, subs deleted, assignments evicted, 202."""
    await _create_client(http_client, auth_headers_ui, "caravan")
    await _create_profile(http_client, auth_headers_ui)
    await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="caravan",
        media_item_id="s1",
        scope_type="show:all",
    )

    response = await http_client.delete("/clients/caravan", headers=auth_headers_ui)

    db_session.expire_all()
    client = await db_session.get(Client, "caravan")
    assignments = list((await db_session.execute(select(Assignment))).scalars())

    assert response.status_code == 202
    assert client is not None
    assert client.decommissioning is True
    assert len(assignments) == 2
    assert all(a.state == "evict" for a in assignments)


async def test_decommission_rejects_new_subscriptions(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """POST /subscriptions for a decommissioning client → 409 Conflict.

    The client must have evict-state assignments when decommissioned so the GC
    pass does NOT immediately delete the client row (GC only fires when the
    assignment count reaches zero after agent confirms evictions).
    """
    await _create_client(http_client, auth_headers_ui, "caravan")
    await _create_profile(http_client, auth_headers_ui)
    # Create a subscription first so assignments exist; they flip to evict on
    # decommission and keep the client row alive until agent confirms.
    await _create_subscription(
        http_client,
        auth_headers_ui,
        client_id="caravan",
        media_item_id="s1",
        scope_type="show:all",
    )

    await http_client.delete("/clients/caravan", headers=auth_headers_ui)

    response = await http_client.post(
        "/subscriptions",
        headers=auth_headers_ui,
        json={
            "client_id": "caravan",
            "media_item_id": "m2",
            "scope_type": "movie",
            "scope_params": None,
            "profile_id": "p1",
        },
    )

    assert response.status_code == 409


async def test_confirm_delivered_rejected_when_evicting(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    """Assignment in evict state → POST /confirm delivered → 409. Assignment stays in evict."""
    client_id = agent_client.id  # capture before expire_all invalidates the object
    asset_id, _ = await _add_assignment(
        db_session,
        client=agent_client,
        files=agent_test_files,
        assignment_state="evict",
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
    assignment = (
        await db_session.execute(
            select(Assignment).where(
                Assignment.client_id == client_id,
                Assignment.asset_id == asset_id,
            )
        )
    ).scalar_one_or_none()

    assert response.status_code == 409
    assert assignment is not None
    assert assignment.state == "evict"


async def test_client_row_gc_after_all_evictions_confirmed(
    http_client: AsyncClient,
    auth_headers_agent: dict[str, str],
    auth_headers_ui: dict[str, str],
    agent_client: Client,
    agent_test_files: AgentTestFiles,
    db_session: AsyncSession,
) -> None:
    """Decommission client → agent confirms all evictions → client row deleted from DB."""
    client_id = agent_client.id  # capture before expire_all invalidates the object
    await _create_profile(http_client, auth_headers_ui, "p-gc")

    # Give the pre-existing agent_client a subscription so the resolver creates an assignment
    response = await http_client.post(
        "/subscriptions",
        headers=auth_headers_ui,
        json={
            "client_id": client_id,
            "media_item_id": "m1",
            "scope_type": "movie",
            "scope_params": None,
            "profile_id": "p-gc",
        },
    )
    assert response.status_code == 201

    # Decommission
    decom_response = await http_client.delete(
        f"/clients/{client_id}", headers=auth_headers_ui
    )
    assert decom_response.status_code == 202

    # At this point assignments are in evict state; client row still exists
    db_session.expire_all()
    client_before = await db_session.get(Client, client_id)
    assert client_before is not None

    # Agent confirms all evictions
    assignments = list((await db_session.execute(select(Assignment))).scalars())
    assert len(assignments) > 0, "Must have assignments to evict"

    for assignment in assignments:
        resp = await http_client.post(
            f"/confirm/{assignment.asset_id}",
            headers=auth_headers_agent,
            json={"state": "evicted"},
        )
        assert resp.status_code == 200

    # Trigger resolve_all_subscriptions so the client-row GC pass runs.
    # POST /subscriptions calls resolve; use a fresh client + profile to avoid conflicts.
    await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": "trigger-gc", "name": "Trigger GC"},
    )
    await http_client.post(
        "/subscriptions",
        headers=auth_headers_ui,
        json={
            "client_id": "trigger-gc",
            "media_item_id": "m1",
            "scope_type": "movie",
            "scope_params": None,
            "profile_id": "p-gc",
        },
    )

    db_session.expire_all()
    client_after = await db_session.get(Client, client_id)
    assert client_after is None, "Decommissioned client with no assignments must be GC'd"
