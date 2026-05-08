from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.models import Asset, Assignment, Client, Profile
from syncarr_server.services.rate_tracker import rate_tracker

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_rate_tracker() -> None:
    rate_tracker._samples.clear()


@pytest.fixture
def session(db_session: AsyncSession) -> AsyncSession:
    return db_session


async def _seed_profile(session: AsyncSession, profile_id: str = "p1") -> None:
    p = Profile()
    p.id = profile_id
    p.name = profile_id
    p.ffmpeg_args = ["-c:v", "libx265"]
    p.target_size_bytes = None
    p.created_at = _NOW
    session.add(p)
    await session.commit()


async def _seed_client(session: AsyncSession, client_id: str = "c1", last_seen: datetime | None = None) -> None:
    c = Client()
    c.id = client_id
    c.name = client_id.title()
    c.auth_token = f"tok-{client_id}"
    c.storage_budget_bytes = None
    c.last_seen = last_seen if last_seen is not None else datetime.now(UTC)
    c.created_at = _NOW
    c.decommissioning = False
    session.add(c)
    await session.commit()


async def _seed_asset(
    session: AsyncSession,
    asset_id: int = 1,
    status: str = "ready",
    size_bytes: int | None = 1_000_000,
    sha256: str | None = "abc",
    profile_id: str = "p1",
    source_media_id: str | None = None,
) -> None:
    a = Asset()
    a.id = asset_id
    a.source_media_id = source_media_id or f"m{asset_id}"
    a.profile_id = profile_id
    a.source_path = f"/mnt/media/movie{asset_id}.mkv"
    a.cache_path = f"/mnt/cache/{asset_id}.mkv"
    a.size_bytes = size_bytes
    a.sha256 = sha256
    a.status = status
    a.status_detail = None
    a.created_at = _NOW
    a.ready_at = _NOW if status == "ready" else None
    session.add(a)
    await session.commit()


async def _seed_assignment(
    session: AsyncSession,
    client_id: str = "c1",
    asset_id: int = 1,
    state: str = "pending",
    bytes_downloaded: int | None = None,
    delivered_at: datetime | None = None,
) -> None:
    a = Assignment()
    a.client_id = client_id
    a.asset_id = asset_id
    a.state = state
    a.created_at = _NOW
    a.delivered_at = delivered_at
    a.evict_requested_at = None
    a.bytes_downloaded = bytes_downloaded
    a.bytes_downloaded_updated_at = None
    a.last_confirm_error_at = None
    a.last_confirm_error_reason = None
    session.add(a)
    await session.commit()


async def test_empty_db_returns_empty_rows(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    resp = await http_client.get("/queue", headers=auth_headers_ui)
    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


async def test_queued_asset_returns_row(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session, status="ready")
    await _seed_assignment(session)
    resp = await http_client.get("/queue", headers=auth_headers_ui)
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["pipeline_status"] == "queued"
    assert rows[0]["client_id"] == "c1"
    assert rows[0]["asset_id"] == 1


async def test_delivered_assignment_returns_ready(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session)
    await _seed_assignment(session, state="delivered", delivered_at=_NOW)
    resp = await http_client.get("/queue", headers=auth_headers_ui)
    assert resp.json()["rows"][0]["pipeline_status"] == "ready"


async def test_evicted_assignment_excluded(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session)
    await _seed_assignment(session, state="evict")
    resp = await http_client.get("/queue", headers=auth_headers_ui)
    assert resp.json()["rows"] == []


async def test_two_clients_same_asset_two_rows(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session, "c1")
    await _seed_client(session, "c2")
    await _seed_asset(session)
    await _seed_assignment(session, client_id="c1")
    await _seed_assignment(session, client_id="c2")
    resp = await http_client.get("/queue", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    assert len(rows) == 2
    assert {r["client_id"] for r in rows} == {"c1", "c2"}


async def test_two_profiles_same_client_same_item_two_rows(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session, "p1")
    await _seed_profile(session, "p2")
    await _seed_client(session)
    await _seed_asset(session, asset_id=1, profile_id="p1", source_media_id="m1")
    await _seed_asset(session, asset_id=2, profile_id="p2", source_media_id="m1")
    await _seed_assignment(session, asset_id=1)
    await _seed_assignment(session, asset_id=2)
    resp = await http_client.get("/queue", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    assert len(rows) == 2
    assert {r["profile_id"] for r in rows} == {"p1", "p2"}


async def test_status_filter_single_value(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session, asset_id=1)
    await _seed_asset(session, asset_id=2)
    await _seed_assignment(session, asset_id=1, state="delivered", delivered_at=_NOW)
    await _seed_assignment(session, asset_id=2)
    resp = await http_client.get("/queue?status=queued", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    assert all(r["pipeline_status"] == "queued" for r in rows)
    assert len(rows) == 1


async def test_status_filter_repeated_values(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session, asset_id=1)
    await _seed_asset(session, asset_id=2)
    await _seed_assignment(session, asset_id=1, state="delivered", delivered_at=_NOW)
    await _seed_assignment(session, asset_id=2)
    resp = await http_client.get("/queue?status=queued&status=ready", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    assert {r["pipeline_status"] for r in rows} == {"queued", "ready"}


async def test_status_filter_invalid_returns_422(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    resp = await http_client.get("/queue?status=invalid", headers=auth_headers_ui)
    assert resp.status_code == 422


async def test_client_id_filter(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session, "c1")
    await _seed_client(session, "c2")
    await _seed_asset(session)
    await _seed_assignment(session, client_id="c1")
    await _seed_assignment(session, client_id="c2")
    resp = await http_client.get("/queue?client_id=c1", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    assert all(r["client_id"] == "c1" for r in rows)


async def test_missing_bearer_returns_401(http_client: AsyncClient) -> None:
    resp = await http_client.get("/queue")
    assert resp.status_code == 401


async def test_sort_transferring_before_queued_before_ready(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session, asset_id=1)
    await _seed_asset(session, asset_id=2)
    await _seed_asset(session, asset_id=3)
    await _seed_assignment(session, asset_id=1, state="delivered", delivered_at=_NOW)
    await _seed_assignment(session, asset_id=2)
    await _seed_assignment(session, asset_id=3, bytes_downloaded=100_000)
    resp = await http_client.get("/queue", headers=auth_headers_ui)
    statuses = [r["pipeline_status"] for r in resp.json()["rows"]]
    assert statuses.index("transferring") < statuses.index("queued")
    assert statuses.index("queued") < statuses.index("ready")


async def test_client_assignments_new_fields_populated(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session)
    await _seed_assignment(session)
    resp = await http_client.get("/clients/c1/assignments", headers=auth_headers_ui)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert "asset_id" in row
    assert "profile_id" in row
    assert "pipeline_status" in row
    assert "pipeline_substate" in row
    assert "pipeline_detail" in row
    assert "state" in row


async def test_client_assignments_multi_profile_two_rows(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session, "p1")
    await _seed_profile(session, "p2")
    await _seed_client(session)
    await _seed_asset(session, asset_id=1, profile_id="p1", source_media_id="m1")
    await _seed_asset(session, asset_id=2, profile_id="p2", source_media_id="m1")
    await _seed_assignment(session, asset_id=1)
    await _seed_assignment(session, asset_id=2)
    resp = await http_client.get("/clients/c1/assignments", headers=auth_headers_ui)
    rows = resp.json()
    assert len(rows) == 2
    assert {r["profile_id"] for r in rows} == {"p1", "p2"}


async def test_cross_projection_queue_matches_client_assignments(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session)
    await _seed_assignment(session, bytes_downloaded=300_000)
    queue_resp = await http_client.get("/queue", headers=auth_headers_ui)
    assign_resp = await http_client.get("/clients/c1/assignments", headers=auth_headers_ui)
    queue_status = queue_resp.json()["rows"][0]["pipeline_status"]
    assign_status = assign_resp.json()[0]["pipeline_status"]
    assert queue_status == assign_status
