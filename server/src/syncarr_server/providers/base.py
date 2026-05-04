from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MediaLibrary:
    provider_id: str
    title: str
    type: str


@dataclass(frozen=True)
class MediaItem:
    provider_id: str
    title: str
    type: str
    year: int | None = None
    file_path: str | None = None
    size_bytes: int | None = None
    parent_id: str | None = None
    season_number: int | None = None
    episode_number: int | None = None


@dataclass(frozen=True)
class MediaItemDetails:
    item: MediaItem
    children: list[MediaItem]


@dataclass(frozen=True)
class MediaPreview:
    item_id: str
    file_count: int
    total_source_size_bytes: int
    estimated_transcoded_size_bytes: int | None = None


class MediaProvider(Protocol):
    def list_libraries(self) -> list[MediaLibrary]: ...

    def browse_library(self, library_id: str, search: str | None = None) -> list[MediaItem]: ...

    def get_item(self, media_item_id: str) -> MediaItemDetails: ...

    def preview_item(self, media_item_id: str) -> MediaPreview: ...

    def expand_scope(
        self,
        media_item_id: str,
        scope_type: str,
        scope_params: dict[str, object] | None,
    ) -> list[MediaItem]: ...
