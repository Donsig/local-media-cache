from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_list_libraries_requires_ui_auth(http_client: AsyncClient) -> None:
    response = await http_client.get("/media/libraries")

    assert response.status_code == 401


async def test_list_libraries(http_client: AsyncClient, auth_headers_ui: dict[str, str]) -> None:
    response = await http_client.get("/media/libraries", headers=auth_headers_ui)

    assert response.status_code == 200
    assert response.json() == {
        "libraries": [
            {"id": "1", "title": "Movies", "type": "movie"},
            {"id": "2", "title": "TV Shows", "type": "show"},
        ],
    }


async def test_list_library_items_supports_search(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    response = await http_client.get(
        "/media/library/1/items",
        params={"search": "arr"},
        headers=auth_headers_ui,
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": "m1",
            "title": "Arrival",
            "type": "movie",
            "year": 2016,
            "file_path": "/mnt/media/movies/Arrival.mkv",
            "size_bytes": 4_000_000_000,
            "parent_id": None,
            "season_number": None,
            "episode_number": None,
        },
    ]


async def test_get_media_item_details(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    response = await http_client.get("/media/item/s1", headers=auth_headers_ui)

    assert response.status_code == 200
    assert response.json() == {
        "item": {
            "id": "s1",
            "title": "Bluey",
            "type": "show",
            "year": 2018,
            "file_path": None,
            "size_bytes": None,
            "parent_id": None,
            "season_number": None,
            "episode_number": None,
        },
        "children": [
            {
                "id": "season-1",
                "title": "Season 1",
                "type": "season",
                "year": None,
                "file_path": None,
                "size_bytes": None,
                "parent_id": "s1",
                "season_number": None,
                "episode_number": None,
            },
            {
                "id": "season-2",
                "title": "Season 2",
                "type": "season",
                "year": None,
                "file_path": None,
                "size_bytes": None,
                "parent_id": "s1",
                "season_number": None,
                "episode_number": None,
            },
        ],
    }


async def test_preview_media_item(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    response = await http_client.get("/media/item/s1/preview", headers=auth_headers_ui)

    assert response.status_code == 200
    assert response.json() == {
        "item_id": "s1",
        "file_count": 2,
        "total_source_size_bytes": 1_200_000_000,
        "estimated_transcoded_size_bytes": None,
    }
