from __future__ import annotations

from typing import Any

from plexapi.server import PlexServer

from syncarr_server.providers.base import MediaItem, MediaItemDetails, MediaLibrary, MediaPreview


class PlexProvider:
    def __init__(
        self,
        base_url: str,
        token: str,
        plex_path_prefix: str | None = None,
        local_path_prefix: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self.plex_path_prefix = plex_path_prefix
        self.local_path_prefix = local_path_prefix
        self._plex: Any = PlexServer(base_url, token)  # type: ignore[no-untyped-call]

    def list_libraries(self) -> list[MediaLibrary]:
        return [
            MediaLibrary(
                provider_id=str(section.key),
                title=str(section.title),
                type=str(section.type),
            )
            for section in self._plex.library.sections()
        ]

    def browse_library(self, library_id: str, search: str | None = None) -> list[MediaItem]:
        section = self._plex.library.sectionByID(int(library_id))
        items = section.search(title=search) if search else section.all()
        return [self._to_media_item(item) for item in items]

    def get_item(self, media_item_id: str) -> MediaItemDetails:
        item = self._plex.fetchItem(int(media_item_id))
        return MediaItemDetails(item=self._to_media_item(item), children=self._children_for(item))

    def preview_item(self, media_item_id: str) -> MediaPreview:
        item = self._plex.fetchItem(int(media_item_id))
        files = self._file_items_for(item)
        return MediaPreview(
            item_id=media_item_id,
            file_count=len(files),
            total_source_size_bytes=sum(file.size_bytes or 0 for file in files),
        )

    def expand_scope(
        self,
        media_item_id: str,
        scope_type: str,
        scope_params: dict[str, object] | None,
    ) -> list[MediaItem]:
        item = self._plex.fetchItem(int(media_item_id))

        if scope_type == "movie":
            return [self._to_media_item(item)]

        if scope_type == "show:all":
            return self._episodes_for(item)

        if scope_type == "show:seasons":
            seasons = scope_params.get("seasons") if scope_params else None
            if not isinstance(seasons, list):
                return []
            wanted = {int(season) for season in seasons}
            return [
                episode
                for episode in self._episodes_for(item)
                if episode.season_number in wanted
            ]

        return []

    def _children_for(self, item: Any) -> list[MediaItem]:
        item_type = str(getattr(item, "type", ""))
        if item_type == "show":
            return [self._to_media_item(season) for season in item.seasons()]
        if item_type == "season":
            return [self._to_media_item(episode) for episode in item.episodes()]
        return []

    def _episodes_for(self, item: Any) -> list[MediaItem]:
        item_type = str(getattr(item, "type", ""))
        if item_type == "show":
            return [self._to_media_item(episode) for episode in item.episodes()]
        if item_type == "season":
            return [self._to_media_item(episode) for episode in item.episodes()]
        if item_type == "episode":
            return [self._to_media_item(item)]
        return []

    def _file_items_for(self, item: Any) -> list[MediaItem]:
        item_type = str(getattr(item, "type", ""))
        if item_type in {"show", "season"}:
            return self._episodes_for(item)
        return [self._to_media_item(item)]

    def _to_media_item(self, item: Any) -> MediaItem:
        return MediaItem(
            provider_id=str(item.ratingKey),
            title=str(item.title),
            type=str(getattr(item, "type", "unknown")),
            year=getattr(item, "year", None),
            file_path=self._source_path(item),
            size_bytes=self._source_size(item),
            parent_id=self._optional_str(getattr(item, "parentRatingKey", None)),
            season_number=getattr(item, "parentIndex", None),
            episode_number=getattr(item, "index", None),
        )

    def _source_path(self, item: Any) -> str | None:
        path = self._first_part_attr(item, "file")
        if not isinstance(path, str):
            return None
        return self._rewrite_path(path)

    def _source_size(self, item: Any) -> int | None:
        size = self._first_part_attr(item, "size")
        if isinstance(size, int):
            return size
        if isinstance(size, str):
            return int(size)
        return None

    def _first_part_attr(self, item: Any, attr: str) -> object | None:
        media = getattr(item, "media", None)
        if not media:
            return None
        parts = getattr(media[0], "parts", None)
        if not parts:
            return None
        return getattr(parts[0], attr, None)

    def _rewrite_path(self, path: str) -> str:
        if not self.plex_path_prefix:
            return path
        if not path.startswith(self.plex_path_prefix):
            return path
        return f"{self.local_path_prefix}{path[len(self.plex_path_prefix):]}"

    def _optional_str(self, value: object | None) -> str | None:
        return str(value) if value is not None else None
