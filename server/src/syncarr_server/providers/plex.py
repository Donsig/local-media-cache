from __future__ import annotations

from syncarr_server.providers.base import MediaItem


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

    def browse_library(self, library_id: str, search: str | None = None) -> list[MediaItem]:
        raise NotImplementedError

    def expand_scope(
        self,
        media_item_id: str,
        scope_type: str,
        scope_params: dict[str, object] | None,
    ) -> list[MediaItem]:
        raise NotImplementedError

