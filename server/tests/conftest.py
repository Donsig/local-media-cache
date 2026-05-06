from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pytest import ExitCode, Session
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("SYNCARR_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from syncarr_server.config import get_settings  # noqa: E402
from syncarr_server.db import get_session  # noqa: E402
from syncarr_server.main import app  # noqa: E402
from syncarr_server.models import Base, Client  # noqa: E402
from syncarr_server.providers.base import (  # noqa: E402
    MediaItem,
    MediaItemDetails,
    MediaLibrary,
    MediaPreview,
)


class MockMediaProvider:
    def __init__(self) -> None:
        self.libraries = [
            MediaLibrary(provider_id="1", title="Movies", type="movie"),
            MediaLibrary(provider_id="2", title="TV Shows", type="show"),
        ]
        self.items = {
            "1": [
                MediaItem(
                    provider_id="m1",
                    title="Arrival",
                    type="movie",
                    year=2016,
                    file_path="/mnt/media/movies/Arrival.mkv",
                    size_bytes=4_000_000_000,
                ),
                MediaItem(
                    provider_id="m2",
                    title="Dune",
                    type="movie",
                    year=2021,
                    file_path="/mnt/media/movies/Dune.mkv",
                    size_bytes=8_000_000_000,
                ),
            ],
            "2": [
                MediaItem(provider_id="s1", title="Bluey", type="show", year=2018),
            ],
        }
        self.details = MediaItemDetails(
            item=MediaItem(provider_id="s1", title="Bluey", type="show", year=2018),
            children=[
                MediaItem(provider_id="season-1", title="Season 1", type="season", parent_id="s1"),
                MediaItem(provider_id="season-2", title="Season 2", type="season", parent_id="s1"),
            ],
        )
        self.preview = MediaPreview(
            item_id="s1",
            file_count=2,
            total_source_size_bytes=1_200_000_000,
            estimated_transcoded_size_bytes=None,
        )
        self.expandable_items = {
            "m1": [
                MediaItem(
                    provider_id="m1",
                    title="Arrival",
                    type="movie",
                    year=2016,
                    file_path="/mnt/media/movies/Arrival.mkv",
                    size_bytes=4_000_000_000,
                ),
            ],
            "m2": [
                MediaItem(
                    provider_id="m2",
                    title="Dune",
                    type="movie",
                    year=2021,
                    file_path="/mnt/media/movies/Dune.mkv",
                    size_bytes=8_000_000_000,
                ),
            ],
            "s1": [
                MediaItem(
                    provider_id="e1",
                    title="Bluey - Dance Mode",
                    type="episode",
                    file_path="/mnt/media/shows/Bluey/Season 2/Bluey - S02E01.mkv",
                    size_bytes=600_000_000,
                    parent_id="season-2",
                    season_number=2,
                    episode_number=1,
                ),
                MediaItem(
                    provider_id="e2",
                    title="Bluey - Bumpy and the Wise Old Wolfhound",
                    type="episode",
                    file_path="/mnt/media/shows/Bluey/Season 2/Bluey - S02E02.mkv",
                    size_bytes=600_000_000,
                    parent_id="season-2",
                    season_number=2,
                    episode_number=2,
                ),
            ],
        }

    def list_libraries(self) -> list[MediaLibrary]:
        return self.libraries

    def browse_library(self, library_id: str, search: str | None = None) -> list[MediaItem]:
        items = self.items.get(library_id, [])
        if search is None:
            return items
        return [item for item in items if search.lower() in item.title.lower()]

    def get_item(self, media_item_id: str) -> MediaItemDetails:
        if media_item_id != self.details.item.provider_id:
            raise KeyError(media_item_id)
        return self.details

    def preview_item(self, media_item_id: str) -> MediaPreview:
        if media_item_id != self.preview.item_id:
            raise KeyError(media_item_id)
        return self.preview

    def expand_scope(
        self,
        media_item_id: str,
        scope_type: str,
        scope_params: dict[str, object] | None,
    ) -> list[MediaItem]:
        items = self.expandable_items.get(media_item_id)
        if items is None:
            raise KeyError(media_item_id)

        if scope_type in {"movie", "show:all"}:
            return items

        if scope_type == "show:seasons":
            if scope_params is None:
                raise ValueError("scope_params required for show:seasons")
            seasons_value = scope_params.get("seasons")
            if not isinstance(seasons_value, list):
                raise ValueError("seasons must be a list")
            seasons = {
                season
                for season in seasons_value
                if isinstance(season, int)
            }
            return [item for item in items if item.season_number in seasons]

        raise ValueError(f"Unsupported scope_type: {scope_type}")


class TestSettings:
    ui_token = "ui-token"
    local_path_prefix = "/mnt/media"


@dataclass
class MockFfmpeg:
    calls: list[list[str]]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    output_bytes: bytes = b"transcoded-output"

    def set_result(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        output_bytes: bytes = b"transcoded-output",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.output_bytes = output_bytes


@dataclass(frozen=True)
class AgentTestFiles:
    cache_path: Path
    source_path: Path
    cache_size_bytes: int
    source_size_bytes: int
    cache_sha256: str
    source_sha256: str


@pytest.fixture
def auth_headers_ui() -> dict[str, str]:
    return {"Authorization": "Bearer ui-token"}


@pytest.fixture
def mock_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> MockFfmpeg:
    mock = MockFfmpeg(calls=[])

    def _run(cmd: list[str], capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        mock.calls.append(list(cmd))
        if mock.returncode == 0:
            Path(cmd[-1]).write_bytes(mock.output_bytes)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=mock.returncode,
            stdout=mock.stdout,
            stderr=mock.stderr,
        )

    monkeypatch.setattr("syncarr_server.transcoder.subprocess.run", _run)
    return mock


@pytest_asyncio.fixture
async def db_session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def agent_client(db_session: AsyncSession) -> Client:
    client = Client(
        id="test-agent",
        name="Test Agent",
        auth_token="agent-test-agent-testtoken123",
        storage_budget_bytes=None,
        last_seen=None,
        created_at=datetime.now(UTC),
        decommissioning=False,
    )
    db_session.add(client)
    await db_session.commit()
    return client


@pytest_asyncio.fixture
async def agent_client_b(db_session: AsyncSession) -> Client:
    client = Client(
        id="test-agent-b",
        name="Test Agent B",
        auth_token="agent-test-agent-b-testtoken123",
        storage_budget_bytes=None,
        last_seen=None,
        created_at=datetime.now(UTC),
        decommissioning=False,
    )
    db_session.add(client)
    await db_session.commit()
    return client


@pytest.fixture
def auth_headers_agent() -> dict[str, str]:
    return {"Authorization": "Bearer agent-test-agent-testtoken123"}


@pytest.fixture
def auth_headers_agent_b() -> dict[str, str]:
    return {"Authorization": "Bearer agent-test-agent-b-testtoken123"}


@pytest.fixture
def agent_test_files(tmp_path: Path) -> AgentTestFiles:
    cache_bytes = bytes(range(256)) * 8
    source_bytes = b"passthrough-source-video"
    cache_path = tmp_path / "cache.mkv"
    source_path = tmp_path / "source.mkv"
    cache_path.write_bytes(cache_bytes)
    source_path.write_bytes(source_bytes)
    return AgentTestFiles(
        cache_path=cache_path,
        source_path=source_path,
        cache_size_bytes=len(cache_bytes),
        source_size_bytes=len(source_bytes),
        cache_sha256=hashlib.sha256(cache_bytes).hexdigest(),
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
    )


@pytest_asyncio.fixture
async def http_client(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def _get_test_session() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            yield session

    app.state.media_provider = MockMediaProvider()
    app.dependency_overrides[get_settings] = TestSettings
    app.dependency_overrides[get_session] = _get_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()
    app.state.media_provider = None


def pytest_sessionfinish(session: Session, exitstatus: int | ExitCode) -> None:
    if session.config.option.collectonly and session.testscollected == 0:
        session.exitstatus = ExitCode.OK
