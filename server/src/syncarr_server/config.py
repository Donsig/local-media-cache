from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNCARR_")

    database_url: str = "sqlite:///syncarr.db"
    media_root: str = "/mnt/media"
    cache_root: str = "/mnt/cache"
    plex_url: str | None = None
    plex_token: str | None = None
    plex_path_prefix: str | None = None
    local_path_prefix: str = Field(default="/mnt/media")


def get_settings() -> Settings:
    return Settings()

