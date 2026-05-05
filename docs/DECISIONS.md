# Architecture Decision Records

Each decision documents what was chosen, what was considered, and why. Keep these short. If a decision is reversed later, append a "Superseded by..." note rather than editing.

## ADR-001: Pull model (agent polls server), not push

**Decision**: Agents always initiate communication with the server. Server never connects to agents.

**Considered**:
- Server-push via webhooks/SSE: lower latency, real-time updates
- Bidirectional WebSocket: combines push + pull
- Pull-only: simple, NAT-friendly, restart-safe

**Why**: Satellites are behind NAT, change networks (Starlink ↔ 4G ↔ home WiFi when towed), and go offline for long stretches. Server-push requires the server to maintain connection state per agent and re-establish connections constantly. Pull is stateless on the server, polling cadence is the only knob, and there's nothing to break when an agent disappears for a week. The latency cost (minutes vs seconds) is irrelevant for a "transfer media files" use case.

## ADR-002: Server is single source of truth; agent never decides what to delete

**Decision**: All state changes are decided by the server. The agent executes.

**Considered**:
- Agent-side eviction policy (LRU on local disk full): more autonomous
- Server-decided with explicit confirmation: predictable, debuggable

**Why**: Predictability and debuggability win over autonomy for a small system. With server-as-truth, the answer to "why did this file get deleted" is always "the server said so, see the audit log." With agent-side policies, you'd have to reason about both the user's subscription intent AND the agent's local heuristics. Not worth the complexity for one user with one caravan.

## ADR-003: Eviction by reference counting (subscription removal), not TTL

**Decision**: Assets are kept as long as at least one assignment references them. No time-based expiration.

**Considered**:
- TTL-based eviction (delete after N days unused): saves disk space
- LRU cache with size cap: bounded storage
- Reference counting: explicit, predictable

**Why**: TTLs and LRUs are surprising. Coming back from a long trip and finding the cache empty because nothing was accessed in 30 days would be infuriating. Reference counting maps directly to user intent: "I subscribed to it, keep it; I unsubscribed, remove it." Disk pressure can be addressed later via per-client storage budgets in the UI.

## ADR-004: Explicit eviction confirmation from agent before assignment row deletion

**Decision**: Server flips assignment to `evict` state. Agent confirms after deleting locally. Only then does the server delete the assignment row and cache file.

**Considered**:
- Server deletes assignment immediately on subscription removal, agent figures out drift on next poll: simpler server logic
- Two-phase commit with explicit confirmation: more requests, more state, but provably correct

**Why**: Without confirmation, an agent that's offline when a subscription is removed would never know to delete the local file. It would need a "what should I have?" reconciliation pass on every poll, comparing local files against server's current truth. That's possible but more code and more error-prone than just keeping the assignment row alive until the agent has done its job.

## ADR-005: HTTP/JSON over a custom protocol

**Decision**: Standard HTTP with JSON bodies and HTTP Range requests for resumability.

**Considered**:
- gRPC: streaming, typed, but adds tooling complexity
- BitTorrent: built for unreliable transfer, swarm-capable
- Custom UDP protocol with FEC: theoretically optimal for lossy links
- HTTP/3 (QUIC): connection migration handles network handoff
- Plain HTTP/1.1 or HTTP/2 with Range: dead simple, universally supported

**Why**: HTTP wins on developer ergonomics, debuggability (curl works), and library support. BitTorrent's value is in swarming, which doesn't apply with one source and one destination. HTTP/3 is technically optimal for the mobile-network case but Python tooling is immature; revisit later if measured drops are problematic. Tailscale tunnel masks most of HTTP/1.1's weaknesses already.

## ADR-006: Delegate downloads to aria2c instead of writing a custom downloader

**Decision**: Agent talks to aria2 via JSON-RPC. aria2 handles all the actual transfer work.

**Considered**:
- Custom Python downloader with httpx + Range + retry logic: full control, ~100 lines of code
- aria2c via JSON-RPC: external dependency, but battle-tested
- libcurl bindings: middle ground

**Why**: For a system that intends to ship to other people eventually, every line of custom networking code is a future bug report. aria2 has been hardened by 15+ years of people downloading Linux ISOs over hotel WiFi. The cost is one extra apt-installable binary on the satellite, which is trivial. The agent becomes a 60-80 line coordinator instead of a 200+ line downloader.

## ADR-007: Single Docker container for the server (MVP)

**Decision**: Server, transcoder, web UI, all in one container with SQLite.

**Considered**:
- Microservices (separate transcoder worker, separate API, Postgres, Redis queue): production-grade
- Monolith with SQLite: simple, deployable as one image

**Why**: MVP. Splitting into services adds operational complexity (orchestration, network between containers, shared volumes for the cache, separate DBs) for zero functional benefit at this scale. SQLite handles the load fine for one user. If the transcoder ever needs to scale horizontally, breaking it out is a refactor, not a redesign — the queue is already abstracted.

## ADR-008: Don't build watch state sync; use the media server's own mechanism

**Decision**: Don't build watch state sync. Delegate to whatever the provider natively offers.

**Why**: Plex Pass syncs watch state and ratings between servers on the same account, automatically, for free. Jellyfin has similar (less polished) mechanisms. Building this ourselves means reading/writing the media server's own database, which is fragile, version-sensitive, and re-invents what the provider already does. When the MVP runs on Plex, just enable cross-server sync in Plex settings. If a provider has no native sync, that's a limitation of that provider — don't paper over it here.

## ADR-009: SQLite for both server and agent

**Decision**: SQLite, not Postgres.

**Why**: Single-writer workload, embedded, zero ops cost. Postgres would be appropriate if we expected concurrent writes from multiple processes, which we don't. The transcode worker writes from a background task in the same process. WAL mode handles the read concurrency from the API. If we ever need Postgres, SQLAlchemy makes the migration trivial (it was chosen partly for this reason).

## ADR-010: Pre-baked transcoded cache (server stores transcoded outputs)

**Decision**: Transcode outputs are kept on the server until no client needs them.

**Considered**:
- On-demand transcode-and-stream (no cache): saves disk, retranscoded every time
- Pre-baked permanent cache: more disk, instant serves

**Why**: Storage at home is cheap; CPU cycles for re-transcoding the same file aren't. Once Bluey S02E01 is transcoded for one client, it's available immediately for any other client that subscribes. Eviction is reference-count driven (ADR-003), so the cache doesn't grow unbounded.

## ADR-011: aria2 is user-managed, not bundled

**Decision**: The agent expects aria2 to be running independently (systemd unit or Docker). It connects via configured RPC endpoint.

**Why**: Bundling daemons inside Python applications is fragile (process management, lifecycle, signal handling). Letting the user manage aria2 with their existing supervision tools is more robust and matches how every other aria2 consumer works (Pyload, Persepolis). Document the dependency clearly.

## ADR-013: Media provider abstraction layer

**Decision**: The server's media server integration lives behind a provider interface. Plex is the first and only implementation. The sync/transcode/eviction core has no direct dependency on Plex-specific code.

**Considered**:
- Bake Plex in directly and extract later: common pattern, means a bigger refactor when "later" arrives
- Abstract from day one: slightly more ceremony upfront, no rewrites later

**Why**: The core sync machinery — resolving subscriptions to file paths, transcoding, transfer, eviction — is genuinely independent of which media server is involved. The only provider-specific operations are: browse library, expand scope to file list, get file path, trigger scan. Isolating these in one module costs maybe 30 lines of interface definition and makes Jellyfin support a new module rather than a diff across the whole codebase. The naming in the DB and API uses `media_item_id` (not `plex_item_id`) for the same reason.

**What the interface looks like** (conceptually):
```python
class MediaProvider(Protocol):
    def get_libraries(self) -> list[Library]: ...
    def get_item(self, item_id: str) -> MediaItem: ...
    def expand_scope(self, item_id: str, scope: str) -> list[MediaItem]: ...
    def get_file_path(self, item_id: str) -> str: ...
    def trigger_scan(self, library_id: str) -> None: ...
```

MVP ships with `PlexProvider` only. The agent also has a provider concept for triggering local scans.

## ADR-014: Installer script served from the server container

**Decision**: The server exposes `GET /install.sh`, a guided bash script (Proxmox Helper Scripts-style) for provisioning a satellite from scratch.

**Considered**:
- Documentation only: tell the user what to install manually
- Separate install script hosted externally (GitHub raw URL): decoupled from server version
- Served from the server itself: version-matched, can embed the server's own URL and generate a token

**Why**: The primary friction for satellite setup is pairing — generating a client token on the server, then pasting it into the agent config. A script served from the server can embed its own URL and walk the user through token creation interactively, eliminating copy-paste errors. Hosting it on the server also ensures the installer always matches the running server version. The Proxmox Helper Scripts style (colored prompts, confirmation steps, progress output) is a well-understood UX for this kind of guided CLI setup. Cost: ~200 lines of bash.

**What the installer does**:
1. Check platform (warn if not Debian/Ubuntu/Raspberry Pi OS)
2. Install system deps: Python 3.12, pip, aria2
3. Optionally install a media server (Plex, Jellyfin — user selects)
4. Download and install the agent from the server's `/agent.tar.gz`
5. Prompt for server URL (pre-filled from the script's own source) and auth token
6. Write `~/.config/syncarr-agent/config.toml`
7. Install and enable systemd service
8. Run a connectivity test against the server
9. Print a summary

## ADR-012: One concurrent download on the agent

**Decision**: aria2 configured for serial downloads, not parallel.

**Why**: Caravan bandwidth is the bottleneck, not server concurrency. Multiple parallel downloads don't make total throughput faster, they just split it. Serial is simpler to reason about, simpler to monitor, and avoids weird interleaved-progress UI states. Parallel within a single file (aria2 `--split`) is fine; multiple files at once is not.

## ADR-018: Transcode profile presets and passthrough

**Decision**: Profiles support four `preset_type` values at the API/UI layer: `passthrough` | `prefer_quality` | `prefer_size` | `custom`. The underlying DB column is `ffmpeg_args` (JSON array, **nullable**):

- **passthrough**: `ffmpeg_args = NULL`. No ffmpeg invocation. Source file served directly. Asset transitions `queued → ready` without a `transcoding` state; `cache_path` stays NULL; the download endpoint serves `source_path` from the NFS mount. GC skips file deletion for passthrough assets (no cache file was created).
- **prefer_quality**: CRF-based H.265, quality-first. Template: `["-c:v", "libx265", "-crf", "23", "-preset", "medium", "-c:a", "aac", "-b:a", "128k"]`
- **prefer_size**: constrained bitrate, smaller output. Template: `["-c:v", "libx265", "-crf", "28", "-maxrate", "2M", "-bufsize", "4M", "-c:a", "aac", "-b:a", "96k"]`
- **custom**: user supplies raw `ffmpeg_args` array directly. Covers hardware-accelerated encoders (`hevc_nvenc`, `hevc_qsv`, `hevc_vaapi`).

`preset_type` is resolved to `ffmpeg_args` by the server before persisting. The DB stores only `ffmpeg_args`.

**Why**: Passthrough is the right choice when sources are already efficiently encoded — a WEBDL H.264 1080p file playing via Plex Direct Play needs no re-encoding. Transcoding it wastes CPU and may not meaningfully reduce file size. Passthrough as a first-class option removes the need to reason about "what ffmpeg args produce a copy."

## ADR-019: Dual provider abstraction — source and destination

**Decision**: The system has two provider boundaries, both abstracted:

- **Source provider** (`MediaProvider`, server-side): the home media server — browse library, expand subscription scope, get file path, trigger scan. ADR-013. MVP: `PlexProvider`.
- **Destination provider** (`ClientProvider`, agent-side): the satellite playback app — determine file placement path, trigger library scan after delivery. MVP: `FolderClientProvider` (drops files at the configured `library_root` with the relative path from the assignment; no explicit scan trigger — playback app auto-scans or user triggers manually).

**Why**: The source and destination are independently variable. You might run Plex at home and Jellyfin on the caravan, or Emby on both, or Plex at home and a bare Kodi on the Pi. Abstracting both sides means neither the server nor the agent contains hardcoded media-server assumptions. For MVP (Plex/Plex), the destination provider is trivially thin — files dropped in the right folder and Plex handles discovery. The abstraction costs nothing upfront and prevents a rewrite when the second destination type is added.

**Config**: `media_provider_type` (default: `"plex"`) selects the source provider at startup. Provider-agnostic field names (`media_server_url`, `media_server_token`, `media_server_path_prefix`) are used in `Settings` — not `plex_url`, `plex_token`. Adding a second provider requires only a new module in `providers/` and an `elif` branch in `main.py` lifespan.

**What a ClientProvider interface looks like** (conceptual, for when a second implementation is needed):
```python
class ClientProvider(Protocol):
    def resolve_path(self, relative_path: str) -> Path: ...      # map server relative_path → local absolute path
    def after_delivery(self, library_id: str) -> None: ...       # trigger scan / notify app (no-op in MVP)
    def after_eviction(self, local_path: Path) -> None: ...      # post-delete hook (no-op in MVP)
```

MVP `FolderClientProvider` implements all three as trivial operations: path join, no-op, no-op. Future `JellyfinClientProvider` would call the Jellyfin scan API in `after_delivery`.
