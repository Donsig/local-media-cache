from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from syncarr_server.config import get_settings
from syncarr_server.routes import agent, installer, media_browse, ui


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.media_provider = None
    if settings.media_server_url and settings.media_server_token:
        if settings.media_provider_type == "plex":
            from syncarr_server.providers.plex import PlexProvider
            app.state.media_provider = PlexProvider(
                base_url=settings.media_server_url,
                token=settings.media_server_token,
                plex_path_prefix=settings.media_server_path_prefix,
                local_path_prefix=settings.local_path_prefix,
            )
        else:
            raise ValueError(
                f"Unknown media_provider_type: {settings.media_provider_type!r}. "
                "Supported: 'plex'"
            )
    yield


app = FastAPI(title="Syncarr Server", lifespan=lifespan)

app.include_router(media_browse.router)
app.include_router(ui.router)
app.include_router(agent.router)
app.include_router(installer.router)
