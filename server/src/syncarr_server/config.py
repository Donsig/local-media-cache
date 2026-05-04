from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:////data/syncarr.db"
    media_cache_path: str = "/mnt/cache"
    ui_token: str = ""
    plex_url: str = ""
    plex_token: str = ""
    plex_path_prefix: str = "/media"
    local_path_prefix: str = "/mnt/media"


@lru_cache
def get_settings() -> Settings:
    return Settings()
