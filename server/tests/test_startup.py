from __future__ import annotations

import pytest
from fastapi import FastAPI

from syncarr_server.config import Settings
from syncarr_server.main import lifespan


async def _consume_lifespan(app: FastAPI) -> None:
    """Enter the lifespan context manager; let any startup exception propagate."""
    async with lifespan(app):
        pass


@pytest.mark.asyncio
async def test_startup_fails_without_ui_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server must refuse to start when UI_TOKEN is empty."""
    monkeypatch.setattr(
        "syncarr_server.main.get_settings",
        lambda: Settings(ui_token=""),
    )
    app = FastAPI()
    with pytest.raises(ValueError, match="UI_TOKEN"):
        await _consume_lifespan(app)
