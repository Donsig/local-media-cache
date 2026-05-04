# HTTP API Specification

All endpoints are HTTP/JSON over a single FastAPI app. Auth via bearer token in `Authorization: Bearer <token>` header.

Two token scopes:
- **UI tokens**: full access, used by the web UI (or admin curl)
- **Agent tokens**: scoped to a single client_id, can only read its own assignments and confirm its own state changes

## Agent endpoints

These are the endpoints the satellite agent calls. Keep this surface as small as possible.

### `GET /assignments`

The primary agent endpoint. Returns everything the agent needs to do.

**Auth**: agent token (scoped to a client_id)

**Response 200**:
```json
{
  "client_id": "caravan",
  "server_time": "2026-05-04T07:30:00Z",
  "assignments": [
    {
      "asset_id": 1234,
      "state": "ready",
      "source_media_id": "12345",
      "filename": "Bluey - S02E01 - Dance Mode.mkv",
      "size_bytes": 5120000000,
      "sha256": "abc123...",
      "download_url": "/download/1234",
      "etag": "1234-1714801800"
    },
    {
      "asset_id": 1235,
      "state": "queued",
      "source_media_id": "12346",
      "filename": "Bluey - S02E02 - Bumpy and the Wise Old Wolfhound.mkv"
    },
    {
      "asset_id": 1100,
      "state": "evict",
      "filename": "Bluey - S01E01 - Magic Xylophone.mkv"
    }
  ],
  "stats": {
    "total_assigned_bytes": 120000000000,
    "ready_count": 18,
    "queued_count": 6,
    "evict_count": 0
  }
}
```

The agent treats this as a full snapshot; no incremental updates. Bumps `clients.last_seen` on the server.

### `POST /confirm/{asset_id}`

Confirm a state transition for an assignment.

**Auth**: agent token

**Request body**:
```json
{
  "state": "delivered" | "evicted",
  "actual_size_bytes": 5120000000,
  "actual_sha256": "abc123..."
}
```

The size/sha256 fields are sent on `delivered` only, for verification. On mismatch, server logs and may flag for re-transfer (defer to v2).

**Response 200**: `{"ok": true}`

**Idempotent**: confirming the same state twice is a no-op.

### `GET /download/{asset_id}`

Stream the transcoded asset. Must support HTTP `Range` requests.

**Auth**: agent token

**Response 200 / 206**:
- `Content-Type: application/octet-stream`
- `Content-Length: <size>`
- `Accept-Ranges: bytes`
- `ETag: <etag>` matching what `/assignments` returned
- `Content-Range: bytes N-M/total` if partial

**Response 412 Precondition Failed**: if `If-Match` header sent and ETag doesn't match (asset was re-transcoded).

**Response 404**: if asset is not in `ready` state. Agent should retry on next poll cycle.

### `POST /reconcile` (optional, deferred)

Agent reports what it actually has on disk. Server reconciles against assignments table.

**Request body**:
```json
{
  "assets_present": [1234, 1235, 1100],
  "total_bytes": 120000000000
}
```

**Response 200**:
```json
{
  "orphans_to_delete": [1099],
  "missing_to_redownload": [1236]
}
```

Run on agent startup and once a day. Cheap insurance against drift.

## UI endpoints

Used by the web UI. Standard CRUD, mostly self-explanatory.

### Clients

- `GET /clients` — list all clients
- `POST /clients` — create client (returns auth token, shown once)
- `PATCH /clients/{id}` — update name, storage_budget_bytes
- `DELETE /clients/{id}` — remove client and cascade subscriptions
- `POST /clients/{id}/rotate-token` — issue new token, invalidate old

### Profiles

- `GET /profiles` — list profiles
- `POST /profiles` — create
- `PATCH /profiles/{id}` — update
- `DELETE /profiles/{id}` — only if no subscriptions reference it

### Subscriptions

- `GET /subscriptions?client_id=X` — list subs (optionally filtered)
- `POST /subscriptions` — create (triggers immediate resolve)
- `PATCH /subscriptions/{id}` — update profile or scope (triggers re-resolve)
- `DELETE /subscriptions/{id}` — remove (triggers re-resolve and eviction)

### Media browsing

The UI needs to browse the master media library to pick content for subscriptions. Routes are provider-agnostic; the server delegates to whichever media provider adapter is configured.

- `GET /media/libraries` — list libraries from the master media server
- `GET /media/library/{id}/items?search=X` — list items, supports search
- `GET /media/item/{item_id}` — full details including seasons/episodes for shows (item_id is provider-specific)
- `GET /media/item/{item_id}/preview` — show what creating a subscription would resolve to (file count, total source size, estimated transcoded size given a profile)

The preview endpoint is what powers the "this will queue 18 episodes ~90GB → ~9GB" hint in the UI.

### Status / monitoring

- `GET /status` — overall: client count, asset count by state, transcode queue depth, cache size on disk
- `GET /assets?status=X` — list assets, optionally filtered, for debug UI
- `GET /assignments?client_id=X` — UI view of assignments (different from agent's `/assignments`)
- `POST /assets/{id}/retry` — manually retry a failed transcode

### Installer

- `GET /install.sh` — returns the satellite agent installer script as `text/x-sh`. Intended to be piped into bash: `bash <(curl -s http://server:port/install.sh)`. The script is a guided interactive installer (Proxmox Helper Scripts-style) that installs Python, aria2, optionally installs/configures the local media server, writes the agent config file with prompted values (server URL, auth token), and registers a systemd service. Must be generated server-side so it can embed the server's own URL into the agent config.

## Error model

Standard JSON errors:

```json
{
  "error": "subscription_not_found",
  "message": "No subscription with id 42",
  "details": {}
}
```

HTTP status codes used: 200, 201, 204, 400, 401, 403, 404, 409, 412, 500. No others.

## OpenAPI / docs

FastAPI generates `/docs` and `/openapi.json` automatically. Use Pydantic models for all request/response bodies. The auto-generated spec is the source of truth for client implementations.
