"""Agent configuration — loaded from TOML file (tomllib, stdlib)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    server_url: str
    token: str
    library_root: Path
    poll_interval_seconds: int = 300
    aria2_host: str = "127.0.0.1"
    aria2_port: int = 6800
    aria2_secret: str = ""
    state_db_path: Path | None = None  # defaults to library_root/.syncarr/state.db


def load(path: Path) -> Config:
    """Load config from a TOML file at *path*."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    raw_state_db = data.get("state_db_path")
    return Config(
        server_url=data["server_url"].rstrip("/"),
        token=data["token"],
        library_root=Path(data["library_root"]),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 300)),
        aria2_host=str(data.get("aria2_host", "127.0.0.1")),
        aria2_port=int(data.get("aria2_port", 6800)),
        aria2_secret=str(data.get("aria2_secret", "")),
        state_db_path=Path(raw_state_db) if raw_state_db else None,
    )
