# Deferred Features

Things explicitly considered and consciously deferred. Not "forgotten" — "decided not to do yet, here's why."

Use this as a queue for post-MVP work and as a reference when a feature request comes up ("we already thought about that, here's what we decided").

## Deferred to post-MVP

### Web UI

The MVP is API-only. Curating subscriptions via curl + sqlite3 is fine for one user proving the system works. The UI is the next major feature after MVP, and probably the most important for reaching "actually pleasant to use."

UI requirements when built:
- Browse media library (via `/media/*` endpoints), search, navigate seasons/episodes
- Create/edit subscriptions with profile selection
- Preview subscription impact ("18 episodes, ~90GB → ~9GB at 5GB profile")
- Per-client dashboard: assigned, downloaded, in-progress, total bytes
- Asset state inspector for debugging
- Manual retry for failed transcodes

Stack: React + Vite, served as static files by FastAPI in production. Talks to existing API.

### Multiple profiles per content scope

Currently a subscription has one profile. Useful future capability: "send the kids a 5GB version, send my office Pi a 15GB version, both from the same source." Schema supports it (assets are keyed on source+profile), just need UI affordance.

### Dynamic subscription scopes

`show:latest:N` ("always have the latest 5 episodes"), `show:season:S:from:E` ("from S04E03 onwards, ongoing"). Requires the resolver to re-run on a schedule or on Plex library updates. Architecturally simple, just deferred for MVP simplicity.

### Media library scan triggering from agent

After delivering or evicting a batch, the agent should poke the local media server to rescan. Otherwise users have to manually refresh, which defeats the polish goal. The agent's media provider config already knows the local server URL/type; triggering a scan is a one-line provider call. Deferred from MVP to keep the agent minimal.

### Source file change detection

If Plex re-imports a file (different bitrate, edition, replacement), the source_plex_id stays but the file is different. Current MVP assumes sources are immutable. Detection mechanism: store source mtime + size on asset row, re-check on resolve, re-transcode if changed.

### Bandwidth throttling

aria2 supports it natively (`--max-overall-download-limit=2M`). Just need a UI for it. Particularly important for 4G data caps.

### Per-client storage budget enforcement

Client row already has `storage_budget_bytes`. Currently only used for UI hints. Could enforce by refusing to resolve subscriptions that would exceed budget, or by warning the user. Decide on policy when building UI.

### Additional media provider implementations (Jellyfin, Emby, etc.)

The provider abstraction (ADR-013) is in place from day one, but only `PlexProvider` is implemented in MVP. `JellyfinProvider` would be the natural second implementation. Each provider needs:
- Library browsing
- Scope expansion (show → episodes)
- File path resolution
- Scan triggering

Watch state sync for non-Plex providers would also need research (Jellyfin has sync via SyncPlay and plugins; quality varies).

### Multi-client tested support

The schema supports multiple clients from day one. The MVP just hardcodes one. Removing the hardcode is trivial; testing it properly requires a second satellite.

### Per-client auth tokens, scoped

Currently all auth is "static token in env var." Real deployment needs per-client tokens that can only access their own assignments. Add when building multi-client.

### `/reconcile` endpoint

Agent reports what it actually has on disk; server detects drift (orphans, missing files). Useful insurance against weird states. Defer until a weird state actually happens once.

### Transcode retry / failure handling

MVP: failed transcodes stay failed, manual intervention required. Post-MVP: configurable retry policy, dead-letter queue, alerting.

### Observability

Structured logs (JSON), metrics endpoint (Prometheus format), trace IDs through requests. Not needed for one user; needed before shipping to others.

## Considered and rejected (not just deferred)

### Server-push (webhooks, SSE, WebSocket)

Decided pull-only is the right model. See ADR-001.

### BitTorrent transport

Considered for resilience reputation. Rejected because the swarming benefit doesn't apply with one source / one client topology. See ADR-005.

### Custom downloader

Considered writing transfer logic in Python. Rejected in favor of aria2c. See ADR-006.

### Microservices architecture

Considered splitting transcoder, API, queue into separate services. Rejected for MVP — monolith is simpler and SQLite handles the load. See ADR-007.

### Building our own watch-state sync

Considered. Rejected because native media server sync covers it. See ADR-008.

### Agent-managed aria2 (forking it from the agent process)

Considered. Rejected because daemon-management-from-app is fragile. User's systemd or Docker is the right place. See ADR-011.

### Postgres / Redis / RabbitMQ

Considered for queueing and DB. Rejected — over-engineering for the workload. SQLite + asyncio task queue is sufficient. Revisit if scaling concerns ever materialize.

### HTTP/3 / QUIC

Technically optimal for the mobile-network case (connection migration through Starlink → 4G handoff). Deferred because Python tooling is immature. Tailscale tunnel + HTTP/2 with Range requests is good enough in practice. Revisit if measured drops cause real pain.

## Open questions (decide before relevant code is written)

### How does the agent discover its own client_id?

Options: env var, config file, derived from auth token (server returns it on first request). Current lean: config file alongside the agent's token. Decide during agent implementation.

### What ffmpeg parameters actually produce a good 5GB transcode?

Profile design is its own rabbit hole. Two-pass H.265 with target bitrate? CRF with size constraint? AV1 (slow but smaller)? Hardware acceleration (depends on home server)? Initial profile will be hand-tuned; eventually a small "profile presets" library would help.

### What happens when the cache directory fills the home server's disk?

Currently nothing prevents this. Need either: (a) configurable max cache size with eviction policy, (b) refusal to queue new transcodes when below threshold, (c) just trust the operator. MVP picks (c). Add safeguards before shipping.

### How are agents bootstrapped?

**Answered**: installer script served at `GET /install.sh` from the Docker container (see ADR-014). The installer is a near-MVP feature, not deferred — bootstrapping friction is the primary UX barrier to the project being usable by anyone other than the author.

### Does the agent need to handle the satellite's Plex being temporarily down?

Plex restart, OS update, etc. The agent's job is to put files in the right place; Plex consuming them is async. Library scan trigger should retry if Plex is unreachable, but file delivery doesn't depend on Plex being up. So: probably no special handling needed.
