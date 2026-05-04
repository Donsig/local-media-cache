from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.auth import require_ui_auth
from syncarr_server.db import get_session
from syncarr_server.providers.base import (
    MediaItem,
    MediaItemDetails,
    MediaLibrary,
    MediaPreview,
    MediaProvider,
)
from syncarr_server.schemas import (
    MediaItemDetailsResponse,
    MediaItemSchema,
    MediaLibrariesResponse,
    MediaLibraryItemsResponse,
    MediaLibrarySchema,
    MediaPreviewResponse,
)

router = APIRouter(prefix="/media", tags=["media"])


def _provider(request: Request) -> MediaProvider:
    provider = getattr(request.app.state, "media_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media provider is not configured",
        )
    return cast(MediaProvider, provider)


def _library_schema(library: MediaLibrary) -> MediaLibrarySchema:
    return MediaLibrarySchema(id=library.provider_id, title=library.title, type=library.type)


def _item_schema(item: MediaItem) -> MediaItemSchema:
    return MediaItemSchema(
        id=item.provider_id,
        title=item.title,
        type=item.type,
        year=item.year,
        file_path=item.file_path,
        size_bytes=item.size_bytes,
        parent_id=item.parent_id,
        season_number=item.season_number,
        episode_number=item.episode_number,
    )


def _details_response(details: MediaItemDetails) -> MediaItemDetailsResponse:
    return MediaItemDetailsResponse(
        item=_item_schema(details.item),
        children=[_item_schema(child) for child in details.children],
    )


def _preview_response(preview: MediaPreview) -> MediaPreviewResponse:
    return MediaPreviewResponse(
        item_id=preview.item_id,
        file_count=preview.file_count,
        total_source_size_bytes=preview.total_source_size_bytes,
        estimated_transcoded_size_bytes=preview.estimated_transcoded_size_bytes,
    )


@router.get(
    "/libraries",
    response_model=MediaLibrariesResponse,
    dependencies=[Depends(require_ui_auth)],
)
async def list_libraries(
    request: Request,
    _session: Annotated[AsyncSession, Depends(get_session)],
) -> MediaLibrariesResponse:
    provider = _provider(request)
    return MediaLibrariesResponse(
        libraries=[_library_schema(library) for library in provider.list_libraries()],
    )


@router.get(
    "/library/{library_id}/items",
    response_model=MediaLibraryItemsResponse,
    dependencies=[Depends(require_ui_auth)],
)
async def list_library_items(
    library_id: str,
    request: Request,
    _session: Annotated[AsyncSession, Depends(get_session)],
    search: str | None = None,
) -> MediaLibraryItemsResponse:
    provider = _provider(request)
    return MediaLibraryItemsResponse(
        items=[_item_schema(item) for item in provider.browse_library(library_id, search)],
    )


@router.get(
    "/item/{item_id}",
    response_model=MediaItemDetailsResponse,
    dependencies=[Depends(require_ui_auth)],
)
async def get_item(
    item_id: str,
    request: Request,
    _session: Annotated[AsyncSession, Depends(get_session)],
) -> MediaItemDetailsResponse:
    provider = _provider(request)
    return _details_response(provider.get_item(item_id))


@router.get(
    "/item/{item_id}/preview",
    response_model=MediaPreviewResponse,
    dependencies=[Depends(require_ui_auth)],
)
async def preview_item(
    item_id: str,
    request: Request,
    _session: Annotated[AsyncSession, Depends(get_session)],
) -> MediaPreviewResponse:
    provider = _provider(request)
    return _preview_response(provider.preview_item(item_id))
