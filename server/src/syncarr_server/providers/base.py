from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MediaItem:
    provider_id: str
    title: str
    file_path: str


class MediaProvider(Protocol):
    def browse_library(self, library_id: str, search: str | None = None) -> list[MediaItem]: ...

    def expand_scope(
        self,
        media_item_id: str,
        scope_type: str,
        scope_params: dict[str, object] | None,
    ) -> list[MediaItem]: ...

