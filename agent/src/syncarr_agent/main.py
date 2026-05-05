"""Entry point and poll loop for the syncarr satellite agent."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import structlog

from syncarr_agent.aria2_client import Aria2Client
from syncarr_agent.client import ServerClient
from syncarr_agent.config import load
from syncarr_agent.reconciler import reconcile
from syncarr_agent.state import StateDB


def run(config_path: Path) -> None:
    config = load(config_path)
    state_db_path = config.library_root / ".syncarr" / "state.db"
    state = StateDB(state_db_path)
    server = ServerClient(config.server_url, config.token)
    aria2 = Aria2Client(config.aria2_host, config.aria2_port, config.aria2_secret)
    log: structlog.stdlib.BoundLogger = structlog.get_logger()

    log.info("agent.start", server=config.server_url)

    while True:
        try:
            response = server.get_assignments()
            log.info(
                "agent.poll",
                ready=response.stats.ready_count,
                queued=response.stats.queued_count,
                evict=response.stats.evict_count,
            )
            reconcile(
                response.assignments,
                state,
                aria2,
                server,
                config.library_root,
                config.token,
                log,
            )
        except Exception as exc:
            log.warning("agent.poll_error", error=str(exc))

        time.sleep(config.poll_interval_seconds)


def cli() -> None:
    parser = argparse.ArgumentParser(description="Syncarr satellite agent")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/etc/syncarr-agent/config.toml"),
        help="Path to config.toml",
    )
    args = parser.parse_args()
    run(args.config)


if __name__ == "__main__":
    cli()
