from __future__ import annotations

from pydantic import BaseModel


class Schema(BaseModel):
    pass


class MediaLibrarySchema(BaseModel):
    id: str
    title: str
    type: str


class MediaLibrariesResponse(BaseModel):
    libraries: list[MediaLibrarySchema]


class MediaItemSchema(BaseModel):
    id: str
    title: str
    type: str
    year: int | None = None
    file_path: str | None = None
    size_bytes: int | None = None
    parent_id: str | None = None
    season_number: int | None = None
    episode_number: int | None = None


class MediaLibraryItemsResponse(BaseModel):
    items: list[MediaItemSchema]


class MediaItemDetailsResponse(BaseModel):
    item: MediaItemSchema
    children: list[MediaItemSchema]


class MediaPreviewResponse(BaseModel):
    item_id: str
    file_count: int
    total_source_size_bytes: int
    estimated_transcoded_size_bytes: int | None = None
