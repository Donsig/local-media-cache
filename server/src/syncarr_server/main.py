from __future__ import annotations

from fastapi import FastAPI

from syncarr_server.routes import agent, installer, media_browse, ui

app = FastAPI(title="Syncarr Server")

app.include_router(media_browse.router)
app.include_router(ui.router)
app.include_router(agent.router)
app.include_router(installer.router)

