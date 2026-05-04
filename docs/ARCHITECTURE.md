# Architecture

## Topology

```
┌─────────────────────────────────────┐         ┌─────────────────────────────────┐
│ HOME (always on, fast network)      │         │ SATELLITE (caravan, flaky link) │
│                                     │         │                                 │
│  ┌──────────────┐                   │         │  ┌──────────────┐               │
│  │ Media server │◄──── reads ────┐  │         │  │ Media server │               │
│  │  (e.g. Plex) │                │  │         │  │  (e.g. Plex) │               │
│  └──────────────┘                │  │         │  └──────────────┘               │
│         ▲                        │  │         │         ▲                       │
│         │ files                  │  │         │         │ files                 │
│         │                        │  │         │         │                       │
│  ┌──────────────────────────────┐│  │         │  ┌──────────────────────────┐   │
│  │  Server (Docker container)   ││  │         │  │  Agent (systemd)         │   │
│  │                              ││  │         │  │                          │   │
│  │  - FastAPI HTTP API          ││         HTTP │  - Polls /assignments    │   │
│  │  - SQLite DB                 │┼──── over ────┼─►│  - Drives aria2c via   │   │
│  │  - Transcode worker (ffmpeg) ││ Tailscale   │  │    JSON-RPC            │   │
│  │  - Transcoded asset cache    ││             │  │  - Confirms state      │   │
│  │  - Media provider adapter    ││             │  │  - Triggers media scan │   │
│  │  - Installer script (/install)││            │  │                        │   │
│  └──────────────────────────────┘│             │  └──────────────────────────┘   │
│         ▲                        │             │         ▲                       │
│         │                        │             │         │                       │
│  ┌──────────────┐                │             │  ┌──────────────┐               │
│  │  Web UI      │ (React, served │             │  │  aria2c      │               │
│  │  (curation)  │  from server)  │             │  │  (daemon)    │               │
│  └──────────────┘                │             │  └──────────────┘               │
└─────────────────────────────────────┘         └─────────────────────────────────┘
```

Communication is **always satellite → home**, never the reverse. This matters because:

- Satellites are behind NAT / change networks / go offline; servers don't
- Tailscale gives us routable IPs but pull-only is simpler operationally
- The agent polling cadence becomes the only knob for "how responsive"

## Components

### Server (home)

A single Docker container running:

1. **FastAPI HTTP API** — endpoints for the web UI and the agents
2. **SQLite database** — source of truth for subscriptions, assets, assignments, clients
3. **Transcode worker** — async background task that consumes the transcode queue and shells out to ffmpeg
4. **Asset cache** — directory of transcoded output files, named by asset ID
5. **Media provider adapter** — abstraction layer over the media server. Plex is the only implementation for MVP, using `python-plexapi`. The adapter exposes provider-agnostic operations: browse library, expand subscription scope to file list, get file paths, trigger library scan. Jellyfin, Emby, etc. would be new implementations of the same interface.
6. **Web UI** — React SPA served as static files from the same FastAPI app
7. **Installer script** — served at `GET /install.sh`; a guided bash script (Proxmox Helper Scripts-style) that provisions a satellite from scratch: installs dependencies, optionally installs/configures the local media server, writes the agent config, and registers a systemd service. Run with: `bash <(curl -s http://server:port/install.sh)`

Everything in one container for MVP. Splitting transcoder into a worker container is a deferred optimization.

### Agent (satellite)

A small Python script run by systemd timer (or as a long-running service polling on an interval):

1. Polls `GET /assignments` periodically (every 5-15 minutes when online)
2. For each assignment, reconciles with local state and aria2's view
3. Delegates downloads to aria2 via JSON-RPC
4. Confirms state transitions via `POST /confirm/{asset_id}`
5. Triggers local media server library scan after delivery batches (via provider adapter config)
6. Maintains a tiny SQLite of `{asset_id → aria2_gid}` mappings for reconciliation

The agent does **not** make decisions about what to keep or delete. The server tells it.

### aria2c (satellite)

Standalone daemon, user-managed (systemd unit or Docker). The agent connects to its RPC endpoint. Configured for:

- Persistent session (`--save-session` + `--input-file`)
- Resume on (default)
- Checksum validation per file
- Bandwidth throttle (configurable, off by default)
- Single concurrent download (caravan bandwidth is bottleneck, parallelism doesn't help)

## Data flow: end-to-end example

User adds subscription "caravan should have Bluey S2-S3 at 5gb_1080p profile":

```
1. UI → POST /subscriptions
   Server: write subscriptions row

2. Server: resolve subscription
   - Query media provider for Bluey S2 + S3 episodes
   - For each episode (24 total), check if asset (source_id, profile) exists
   - Create missing assets in 'queued' state
   - Create assignments (caravan_client_id, asset_id, 'pending') for each

3. Transcode worker picks up queued assets
   - For each: ffmpeg with profile params, write to cache dir
   - On completion: asset.status = 'ready', asset.sha256 computed
   - On failure: asset.status = 'failed', logged

4. Caravan agent polls GET /assignments (next cycle, e.g. 10 minutes later)
   - Server returns list of assignments with current state
   - 'ready' assets include download URL + sha256 + size
   - 'queued' assets returned with no URL (agent ignores until ready)

5. Agent processes 'ready' list
   - For each: aria2.addUri(url, checksum, dir, out)
   - Tracks gid in local sqlite

6. aria2 downloads, resumes on drops, validates checksum
   - Agent polls aria2.tellStopped() periodically
   - On successful completion: POST /confirm/{asset_id} {state: "delivered"}

7. After batch complete, agent triggers local media server library scan

8. User removes subscription "Bluey S2"
   - Server: delete subscriptions row
   - Server: re-resolve all subscriptions, find assignments no longer needed
   - Server: flip those assignments to 'evict' (don't delete row yet)

9. Agent polls, sees 'evict' assignments
   - Deletes local file
   - POST /confirm/{asset_id} {state: "evicted"}
   - Server: delete assignment row
   - Server: if asset has zero remaining assignments, delete cached transcode

10. Agent triggers media server library scan to remove orphans
```

Every step is idempotent and resumable from any failure point.

## Key invariants

These must hold at all times:

1. **An asset is kept iff at least one assignment references it.** Eviction is automatic and immediate when the last reference drops.
2. **An assignment exists until the agent confirms the file is gone.** Server cannot delete assignment rows on its own initiative; only on agent confirmation.
3. **The server never pushes; the agent always pulls.** No webhooks back to the satellite, no SSH from server to agent. All comms initiated by agent.
4. **Every state transition is idempotent.** Re-confirming "delivered" or "evicted" is a no-op, not an error.
5. **The agent has no opinions.** If the server's `/assignments` response disagrees with local state, server wins. Agent reconciles toward the server view.

## Resilience model

The system assumes:
- Satellite goes offline for arbitrary periods (hours to weeks)
- Individual transfers drop mid-download (Starlink dropouts, 4G handoff)
- Agent or server restarts at any time
- Power cuts on either end

What's required to handle this:

- HTTP Range request support on `/download/{asset_id}` (FastAPI `FileResponse` handles this)
- aria2 handles resume, retry, and chunk validation automatically
- Server holds transcoded assets indefinitely (no TTL); only subscription removal triggers eviction
- `last_seen` on client tracks liveness for diagnostics; never used to auto-cleanup
- All state changes confirmed via separate request after success; resumable if confirm fails

## What this is NOT

- Not a streaming proxy. Files are pre-transferred, then played locally.
- Not a real-time sync. Polling cadence is minutes, not seconds.
- Not a P2P system. One source, many destinations (BitTorrent considered and rejected).
- Not a media server replacement. Both ends run a real media server; this orchestrates around it.
- Not a watch-state syncer. The media server's own sync handles that (Plex's native cross-server sync for Plex; provider-equivalent for others).
- Not Plex-specific. The core mechanics (transcode, transfer, evict) work with any provider the adapter layer supports.
