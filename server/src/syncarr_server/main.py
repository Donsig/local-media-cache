from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from syncarr_server.config import get_settings
from syncarr_server.db import AsyncSessionLocal
from syncarr_server.models import Profile
from syncarr_server.routes import agent, installer, media_browse, ui
from syncarr_server.transcoder import PassthroughWorker, TranscodeWorker

_DEFAULT_PROFILES: list[dict[str, object]] = [
    {
        "id": "passthrough",
        "name": "No Transcode (copy original)",
        "ffmpeg_args": None,
        "target_size_bytes": None,
    },
    {
        "id": "h265-720p",
        "name": "Compact — 720p H.265",
        "ffmpeg_args": [
            "-vf", "scale=-2:720",
            "-c:v", "libx265", "-crf", "28", "-preset", "fast",
            "-c:a", "aac", "-b:a", "96k",
        ],
        "target_size_bytes": None,
    },
    {
        "id": "h265-1080p",
        "name": "Standard — 1080p H.265",
        "ffmpeg_args": [
            "-c:v", "libx265", "-crf", "23", "-preset", "medium",
            "-c:a", "aac", "-b:a", "128k",
        ],
        "target_size_bytes": None,
    },
]


async def _seed_default_profiles() -> None:
    """Insert built-in profiles if they don't already exist. Idempotent."""
    from datetime import UTC, datetime

    async with AsyncSessionLocal() as session:
        for spec in _DEFAULT_PROFILES:
            existing = await session.get(Profile, spec["id"])
            if existing is None:
                session.add(Profile(
                    id=spec["id"],
                    name=spec["name"],
                    ffmpeg_args=spec["ffmpeg_args"],
                    target_size_bytes=spec["target_size_bytes"],
                    created_at=datetime.now(UTC),
                ))
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    worker = TranscodeWorker(AsyncSessionLocal, settings)
    passthrough_worker = PassthroughWorker(AsyncSessionLocal, settings)
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
    await _seed_default_profiles()
    await worker.startup_recovery()
    task = asyncio.create_task(worker.start())
    passthrough_task = asyncio.create_task(passthrough_worker.start())
    try:
        yield
    finally:
        await asyncio.gather(worker.stop(), passthrough_worker.stop())
        await asyncio.gather(task, passthrough_task, return_exceptions=True)


app = FastAPI(title="Syncarr Server", lifespan=lifespan)

repo_ui_dist_dir = Path(__file__).resolve().parents[3] / "ui" / "dist"
container_ui_dist_dir = Path(__file__).resolve().parents[2] / "ui" / "dist"
ui_dist_dir = container_ui_dist_dir if container_ui_dist_dir.exists() else repo_ui_dist_dir

app.include_router(media_browse.router, prefix="/api")
app.include_router(ui.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(installer.router, prefix="/api")
app.include_router(media_browse.router, include_in_schema=False)
app.include_router(ui.router, include_in_schema=False)
app.include_router(agent.router, include_in_schema=False)
app.include_router(installer.router, include_in_schema=False)
app.mount("/", StaticFiles(directory=str(ui_dist_dir), html=True, check_dir=False), name="ui")
