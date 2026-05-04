from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pytest import ExitCode, Session

os.environ.setdefault("SYNCARR_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from syncarr_server.config import get_settings  # noqa: E402
from syncarr_server.main import app  # noqa: E402
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
        return []


class TestSettings:
    ui_token = "ui-token"


@pytest.fixture
def auth_headers_ui() -> dict[str, str]:
    return {"Authorization": "Bearer ui-token"}


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    app.state.media_provider = MockMediaProvider()
    app.dependency_overrides[get_settings] = TestSettings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()
    app.state.media_provider = None


def pytest_sessionfinish(session: Session, exitstatus: int | ExitCode) -> None:
    if session.config.option.collectonly and session.testscollected == 0:
        session.exitstatus = ExitCode.OK
