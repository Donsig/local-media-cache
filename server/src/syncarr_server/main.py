from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from syncarr_server.config import get_settings
from syncarr_server.providers.plex import PlexProvider
from syncarr_server.routes import agent, installer, media_browse, ui


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.media_provider = None
    if settings.plex_url and settings.plex_token:
        app.state.media_provider = PlexProvider(
            base_url=settings.plex_url,
            token=settings.plex_token,
            plex_path_prefix=settings.plex_path_prefix,
            local_path_prefix=settings.local_path_prefix,
        )
    yield


app = FastAPI(title="Syncarr Server", lifespan=lifespan)

app.include_router(media_browse.router)
app.include_router(ui.router)
app.include_router(agent.router)
app.include_router(installer.router)
