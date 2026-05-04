# MVP Scope

The goal of MVP is to validate the architecture by syncing a real test case end-to-end. Not "ship to other people," not "polished UI." Just: prove the bones work.

## Test case (success criterion)

> "Subscribe my caravan client to Bluey S2 and S3 at a 5GB-per-episode profile. Verify they transcode, transfer, and play in the satellite Plex. Then unsubscribe S2, verify those files get removed from the satellite. Take the satellite offline for 24 hours mid-transfer, bring it back, verify it resumes."

If that works, the MVP is done.

## Cut from MVP

These are explicitly out of scope for v1:

- Web UI (use raw curl + sqlite3 CLI for now)
- Multiple clients (one client, hardcoded)
- Multiple profiles (one profile, hardcoded)
- Authentication beyond "static token in env var"
- Plex library scan triggering (do it manually in Plex UI)
- Watch state sync (rely on Plex's native cross-server sync)
- The `/reconcile` endpoint
- Dynamic scopes (`show:latest:N`, `show:season:S:from:E`)
- Source file change detection
- Transcode retry / failure recovery beyond manual intervention
- Bandwidth throttling
- Stale partial file cleanup on agent
- Per-client storage budget enforcement

## Build order

### Stage 1: Server skeleton (1 evening)

- FastAPI project structure
- SQLite schema (clients, profiles, subscriptions, assets, assignments)
- Hardcode one client and one profile via init script
- `PlexProvider` implementation: connect to home Plex, list a library (first implementation of the media provider interface)
- Test: `curl /media/library/{id}/items?search=Bluey` returns Bluey

### Stage 2: Subscription resolver (1 evening)

- `POST /subscriptions` with scope=`show:seasons:[2,3]`
- Resolver function that calls `provider.expand_scope()` to walk episodes and creates assets + assignments
- All assets stay in `queued` state for now
- Test: create sub, verify 24 assets and assignments exist in DB

### Stage 3: Transcode worker (1-2 evenings)

- Background asyncio task that polls for queued assets
- Shells out to ffmpeg with hardcoded args producing roughly 5GB output
- Updates asset state through queued → transcoding → ready
- Computes sha256 and size on completion
- Test: queued assets transition to ready, files exist in cache dir

### Stage 4: Agent endpoints (1 evening)

- `GET /assignments` returning current state for a client
- `GET /download/{asset_id}` with Range support (FastAPI FileResponse handles it)
- `POST /confirm/{asset_id}` for delivered/evicted
- Test: hand-craft curl calls, verify state transitions in DB

### Stage 5: Agent script (1-2 evenings)

- Standalone Python script
- Polls `/assignments`, talks to aria2 via `aria2p`
- Handles `ready` → addUri to aria2
- Handles `evict` → remove from aria2 + delete file + confirm
- Local sqlite for `{asset_id → gid}` mapping
- Systemd timer or simple loop with sleep
- Test: deploy to caravan Pi, watch it download Bluey episodes

### Stage 6: Eviction flow (1 evening)

- `DELETE /subscriptions/{id}` triggers resolver re-run
- Resolver flips orphaned assignments to `evict`
- Agent picks up `evict`, deletes, confirms
- Server removes assignment row, GCs orphan assets and cache files
- Test: delete S2 sub, verify S2 files removed from caravan, S3 untouched

### Stage 7: Resilience validation (1 evening)

- Pull network plug mid-transfer, verify aria2 resumes
- Stop agent, restart, verify state reconciles
- Restart server, verify nothing breaks
- Run for a full week without intervention, verify nothing accumulates cruft

## Tech stack (MVP, opinionated)

- **Server**: Python 3.12, FastAPI, SQLAlchemy 2.x with SQLite, `python-plexapi`, `httpx`
- **Transcode**: subprocess + ffmpeg (system binary)
- **Agent**: Python 3.12, `aria2p`, `httpx`, sqlite3 stdlib
- **Aria2**: distro package on the satellite, configured via systemd unit
- **Container**: Docker for server, plain Python venv for agent (Pi 4 doesn't need Docker overhead)
- **Network**: existing Tailscale tailnet between home and caravan

Nothing exotic. Everything debuggable with standard tools.

## Initial directory layout (suggested)

```
syncarr/
├── server/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── alembic/                  # migrations
│   ├── src/syncarr_server/
│   │   ├── __init__.py
│   │   ├── main.py               # FastAPI app
│   │   ├── db.py                 # SQLAlchemy setup
│   │   ├── models.py             # ORM models
│   │   ├── schemas.py            # Pydantic models
│   │   ├── providers/
│   │   │   ├── base.py           # MediaProvider Protocol
│   │   │   └── plex.py           # PlexProvider (python-plexapi)
│   │   ├── resolver.py           # subscription → assignments (provider-agnostic)
│   │   ├── transcoder.py         # ffmpeg worker
│   │   ├── routes/
│   │   │   ├── agent.py          # /assignments, /confirm, /download
│   │   │   ├── ui.py             # /clients, /profiles, /subscriptions
│   │   │   ├── media_browse.py   # /media/*
│   │   │   └── installer.py      # /install.sh, /agent.tar.gz
│   │   └── auth.py
│   └── tests/
├── agent/
│   ├── pyproject.toml
│   ├── src/syncarr_agent/
│   │   ├── __init__.py
│   │   ├── main.py               # poll loop
│   │   ├── aria2_client.py
│   │   ├── state.py              # local sqlite
│   │   └── plex_scanner.py       # trigger local scans
│   ├── systemd/
│   │   ├── syncarr-agent.service
│   │   └── aria2.service
│   └── tests/
├── docs/                         # this directory
└── README.md
```

## Stretch goals (post-MVP, pre-distribution)

In rough order:

1. **Installer script** (`GET /install.sh`) — Proxmox Helper Scripts-style guided bash installer for satellite setup; served from the Docker container so it's version-matched and can embed the server's own URL (see ADR-014)
2. Web UI (React, served by FastAPI as static files)
3. Multi-client support (the schema already supports it; just remove the hardcoded ID)
4. Multiple profiles + per-subscription profile selection
5. Per-client auth tokens with scoped access
6. The `/reconcile` endpoint
7. Media library scan triggering from agent
8. Automated transcode retry on failure
9. Bandwidth throttling config
10. Dynamic scopes (`show:latest:N`)
11. Source file change detection
12. Additional media provider (Jellyfin) — the abstraction is already in place
13. Better error reporting / observability (structured logs, metrics)
14. Documentation, docker-compose example
