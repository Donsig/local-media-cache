from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:////data/syncarr.db"
    media_cache_path: str = "/mnt/cache"
    ui_token: str = ""
    media_provider_type: str = "plex"       # "plex" | future: "jellyfin", "emby"
    media_server_url: str = ""              # base URL of the source media server
    media_server_token: str = ""            # API token for the source media server
    media_server_path_prefix: str = "/media"  # path prefix as the media server reports it
    local_path_prefix: str = "/mnt/media"     # same path as seen inside the container


@lru_cache
def get_settings() -> Settings:
    return Settings()
