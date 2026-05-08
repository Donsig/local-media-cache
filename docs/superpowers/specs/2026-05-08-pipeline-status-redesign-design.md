# Pipeline Status Redesign — Design Spec

- Created: 2026-05-08
- Revision: v3 (post-Codex-review v2)
- Status: Draft, awaiting third Codex review
- Author: brainstorm session (Claude Code) with user @anders
- Scope: server (`server/`) + UI (`ui/`); no agent changes
- Related: bug #28 (UI pill accuracy, fixed 2026-05-08), bug #34 (etag/abs URL, separate ticket)

## Revision history

- **v1 (2026-05-08, commit `5f129c0`)** — initial draft.
- **v2 (commit `cb669a3`)** — addressed Codex v1 review (resolved all 8 v1 blockers; 12 of 15 improvements resolved).
- **v3 (this revision)** — addresses Codex v2 review. Changes:
  - **NB1 fix:** `RateTracker` is keyed by the composite assignment identity `(client_id, asset_id)` — `Assignment` has no integer surrogate id (composite PK).
  - **NB2 fix:** Server now owns a defined `agent_poll_interval_seconds` setting (default 300s, matching agent default) used as the basis for stalled and offline thresholds.
  - **NB3 fix:** Specified `BulkSyncPill` aggregation rule for show/season subscriptions that fan out to many episode assignments.
  - **I13 fix:** Added operator-visible signal for confirm mismatches (two new assignment columns `last_confirm_error_at` + `last_confirm_error_reason`; projection surfaces a recent error in the detail line).
  - Fixed §5.3 `assignment.size_bytes` → `asset.size_bytes` (size lives on `Asset`).
  - Sort key on `GET /api/queue` is explicitly `Assignment.created_at desc`.
  - `?status=invalid` returns **422** (FastAPI/Pydantic `Literal` native).
  - `§8.3` idempotency wording reconciled with §5.3 strictly-increasing rule.
  - Replaced fragile line-number references with function names + path.
  - Resolved Codex v2 question on multi-profile pill label.

## 1. Problem

The Queue UI lies about file delivery state. Today every row's badge is driven by `Asset.status`, which represents only the *server-side cache* lifecycle (`queued → transcoding → ready → failed`). Once the server has transcoded (or passthrough'd) a file into its cache, the asset becomes `ready` — even though the file may not have been picked up by the satellite, may be 30% downloaded, or may have failed mid-transfer.

In production this surfaces as `[ready]` badges next to progress bars showing 30% complete (verified by user screenshot 2026-05-08). Operators have repeatedly seen "Ready" while the file isn't on the satellite disk.

The root cause is a two-axis problem collapsed into one label:

- **Server cache axis** — `Asset.status` (`queued | transcoding | ready | failed`)
- **Per-client delivery axis** — `Assignment.state` (`pending | delivered | evict`) plus `Assignment.bytes_downloaded`, `Assignment.evict_requested_at`

The UI projects only the first axis onto the badge. This spec replaces that with a single projection over both axes.

A previous narrower fix (bug #28, commit `b147e2e`) corrected the *Library pill* by mapping `pending+asset_ready → "queued"` in `list_client_assignments`. That logic is now duplicated and inconsistent with the Queue surface. This spec consolidates both surfaces behind one server-side projection.

## 2. Goals

1. Queue rows reflect the **delivery pipeline** (server → wire → satellite disk), not the server cache lifecycle.
2. A row marked `ready` corresponds to an `Assignment` in `delivered` state, i.e. the agent has SHA-verified (or size-verified, for passthrough) the file on disk and POSTed `confirm`. **Once delivered, the row remains `ready` regardless of subsequent server-cache state changes** — the file is on the satellite; transcode failures or cache-file drift on the server cannot un-deliver it.
3. Library pills and Queue rows render via a **single** server-side projection function — eliminating the bug class that produced #28.
4. Surface operator-relevant pipeline conditions that today silently masquerade as healthy: **stalled transfers**, **agent offline**, and **recent confirm mismatch**.
5. Surface **transfer rate + ETA** during active transfers so operators can decide whether to wait or switch network (Starlink ↔ 4G).
6. API field changes on existing endpoints are **additive**; old fields stay until a separate cleanup ticket retires them.

## 3. Non-Goals

- Agent ↔ server contract changes. Agent does not get new endpoints or new payloads in this spec.
- No changes to `GET /api/assets` semantics — it remains the per-asset cache view used by LibraryScreen tree rows.
- No new color tokens; reuse existing badge colors.
- Aria2 backpressure visibility (active vs waiting GIDs) is **deferred** to backlog; requires an agent contract change.
- Real-time stale-Ready detection on the satellite (file deleted on disk between confirms) — `/reconcile` already covers this on agent restart + 24h cycle. **Server-side cache file drift** (cache file deleted while assignments still reference it) is also out of scope here; pure projection should not do filesystem IO. A separate cache-integrity check is a future ticket.
- Pagination on `GET /api/queue` — deferred. Current scale (single satellite, hundreds of assets) doesn't need it.
- Server-internal retry/recovery flows for terminal failure classes beyond what already exists (transcode failure stays terminal until operator action).
- Bug #34 (etag and absolute download URL) — separate ticket.
- Bug #37 (DELETE /assets wiping subscriptions) — separate ticket.

## 4. Status Taxonomy

The four primary `pipeline_status` values:

| Status | Meaning | Trigger |
|---|---|---|
| `queued` | The file is not yet being actively transferred to this client. | Asset is `queued`/`transcoding`, OR asset `ready` but agent hasn't begun pulling, OR agent offline. |
| `transferring` | Aria2 has the file in flight (or is verifying it post-download). | Asset `ready`, assignment `pending`, `bytes_downloaded > 0`. |
| `ready` | The file is verified on the satellite's disk. | Assignment `state == "delivered"`. **Terminal.** |
| `failed` | A terminal failure operator must address. | Asset `status == "failed"` while no assignment is delivered. |

Rows where the assignment has `evict_requested_at IS NOT NULL` or `state == "evict"` are **invisible** to Queue and Library pill surfaces. Decommissioning and subscription-deletion progress remains visible on the **Clients screen** (`GET /api/clients`); deeper visibility (per-client evicting count) is a follow-up.

A secondary `pipeline_substate` enum carries finer detail. The UI uses it for styling and as the source for the human-readable detail line:

| Substate | Parent status | Detail string (example) |
|---|---|---|
| `transcoding_pending` | `queued` | "waiting to transcode" |
| `transcoding` | `queued` | "transcoding on server" |
| `waiting_for_agent` | `queued` | "waiting for agent to pick up" |
| `agent_offline` | `queued` | "agent offline (last seen 1h ago)" |
| `downloading` | `transferring` | null (or "last attempt failed: checksum mismatch — retrying" when recent confirm error, see §5.7) |
| `verifying` | `transferring` | "verifying" (sha256 if asset has one; size only for passthrough) |
| `stalled` | `transferring` | "stalled — no progress in 8m" |
| `delivered` | `ready` | null |
| `transcode_failed` | `failed` | "transcode failed: …" |

The substate `verifying` is intentionally generic. Detail-line wording for transcoded assets reads "verifying checksum"; for passthrough assets (where `Asset.sha256 IS NULL`) it reads "verifying size". The substate label itself does not branch on this — only the rendered detail string.

## 5. Architecture

### 5.1 Single projection function

A new module `server/src/syncarr_server/pipeline.py` exports one pure function:

```python
PipelineStatus = Literal["queued", "transferring", "ready", "failed"]
PipelineSubstate = Literal[
    "transcoding_pending", "transcoding", "waiting_for_agent",
    "agent_offline", "downloading", "verifying", "stalled",
    "delivered", "transcode_failed",
]

@dataclass(frozen=True)
class RateSample:
    at: datetime
    bytes_downloaded: int

@dataclass(frozen=True)
class PipelineProjection:
    visible: bool
    status: PipelineStatus | None       # None when visible=False
    substate: PipelineSubstate | None
    detail: str | None
    bytes_downloaded: int | None
    size_bytes: int | None
    transfer_rate_bps: float | None
    eta_seconds: float | None

def project(
    asset: Asset,
    assignment: Assignment | None,
    client: Client | None,
    *,
    now: datetime,
    poll_interval_seconds: int,
    rate_samples: Sequence[RateSample] = (),
    confirm_error_recent_window_seconds: int = 3600,
) -> PipelineProjection: ...
```

Properties:

- **Pure.** No DB writes, no network, no filesystem. Deterministic for `(asset, assignment, client, now, poll_interval_seconds, rate_samples, confirm_error_recent_window_seconds)`. Rate samples are an explicit input so the function stays referentially transparent.
- **Total.** Returns a value for every combination of inputs; never raises on valid ORM state.
- **Single source of truth.** Both `GET /api/queue` and `GET /api/clients/{id}/assignments` use this function.
- **Fully unit-tested** with one test per row of the truth table (§5.2) plus the §5.7 detail enhancement.

Time-dependent decisions (stalled, agent_offline, recent-confirm-error) take `now` as a parameter so tests can pin time without monkey-patching.

### 5.2 Decision truth table

Evaluated top to bottom; first matching row wins.

| # | precondition | → status | → substate | → detail / notes |
|---|---|---|---|---|
| 1 | `assignment is None` | – | – | `visible=False` (no row to render) |
| 2 | `assignment.state == "evict"` OR `assignment.evict_requested_at IS NOT NULL` | – | – | `visible=False` |
| 3 | `assignment.state == "delivered"` | `ready` | `delivered` | null. **Wins over asset.status=failed.** |
| 4 | `asset.status == "failed"` | `failed` | `transcode_failed` | "transcode failed: {asset.status_detail or 'unknown error'}" |
| 5 | `asset.status == "queued"` | `queued` | `transcoding_pending` | "waiting to transcode" |
| 6 | `asset.status == "transcoding"` | `queued` | `transcoding` | "transcoding on server" |
| 7 | client offline (see §5.4) | `queued` | `agent_offline` | "agent offline (last seen Xm ago)". **Wins over rows 8–11.** |
| 8 | `asset.size_bytes IS NULL` | `queued` | `waiting_for_agent` | "waiting for agent to pick up" (size unknown — agent hasn't picked it up yet) |
| 9 | `assignment.bytes_downloaded` is null or `≤ 0` | `queued` | `waiting_for_agent` | "waiting for agent to pick up" |
| 10 | `assignment.bytes_downloaded ≥ asset.size_bytes` | `transferring` | `verifying` | detail string per §4 (checksum vs size) |
| 11 | `0 < assignment.bytes_downloaded < asset.size_bytes` AND **stalled** (see §5.3) | `transferring` | `stalled` | "stalled — no progress in Xm" |
| 12 | `0 < assignment.bytes_downloaded < asset.size_bytes` (otherwise) | `transferring` | `downloading` | null *or* recent-confirm-error detail (§5.7) |

Precedence rationale:

- **Row 3 before 4** — once a file is on the satellite, transcode failure on the server cannot unwrite it. The operator's correct mental model is "this delivery is done".
- **Row 7 before 8–11** — when the agent isn't polling, "stalled" or "downloading" is misleading; the actionable signal is "go check the satellite". Stalled, in-progress, and pending all collapse into `agent_offline`.
- **Invalid byte combinations** — `bytes_downloaded < 0` is treated as 0 (defensive; should never happen). `bytes_downloaded > size_bytes` is clamped to size for the row 10 check (treated as verifying). Negative size is impossible (DB constraint). These are documented in tests.

### 5.3 Stalled detection

A transfer is **stalled** iff:

```
assignment.bytes_downloaded > 0
  AND assignment.bytes_downloaded_updated_at IS NOT NULL
  AND now - assignment.bytes_downloaded_updated_at > max(2 * poll_interval_seconds, 120s)
```

`bytes_downloaded_updated_at` is a new nullable column on `assignments` (§6.4). Null means "no progress timestamp yet" — explicitly **not stalled** (could be a freshly-migrated row or a transfer that hasn't reported yet).

`poll_interval_seconds` is the server's **assumed agent poll cadence**, sourced from `Settings.agent_poll_interval_seconds` (default `300`, mirroring the agent's own default at `agent/src/syncarr_agent/config.py:15`). Fixing this on the server is acceptable because:
- All deployed agents currently use the default (`300`).
- If an agent runs faster, the threshold is conservative (slower to flag stalled); not a correctness issue.
- A per-client poll interval would require an agent contract change; out of scope (§3).

The threshold is `max(2 × poll_interval, 120s)` so a single missed poll doesn't trigger stalled.

**Update rules** for `bytes_downloaded_updated_at` (writer path: function `update_assignment_progress` in `server/src/syncarr_server/routes/agent.py`, which handles `PATCH /assignments/{asset_id}/progress`):

- Set timestamp to `now` whenever `payload.bytes_downloaded > assignment.bytes_downloaded`. This is the ONLY case that bumps the timestamp.
- Equal values: no change to value or timestamp (heartbeat; `bytes_downloaded_updated_at` does not advance).
- Lower values: stored value not lowered; timestamp not bumped; warning logged.
- Negative values: 422 (FastAPI/Pydantic constraint on the request schema).
- `payload.bytes_downloaded > asset.size_bytes` (over-size): accept and store, treat as verifying. Log at INFO (this can happen when aria2's reported total includes overhead).

These are server-side validations; agent code unchanged.

### 5.4 Agent offline detection

A client is offline iff:

```
client.last_seen IS NULL
  OR (now - client.last_seen) > max(3 * poll_interval_seconds, 180s)
```

Reuses the existing `clients.last_seen` column (`server/src/syncarr_server/models.py:22`); **no new column needed**. `poll_interval_seconds` is the same setting as §5.3.

`last_seen` is currently bumped on `GET /assignments` poll (`server/src/syncarr_server/routes/agent.py`, function `list_assignments`). This spec **does not widen** that write surface — `last_seen` continues to be poll-only, not bumped on `/download`, `/progress`, or `/confirm`. Rationale (per ADR-009 single-writer SQLite): Range-retry storms on `/download` would otherwise turn file serving into DB writes; limiting `last_seen` writes to the polling endpoint keeps contention bounded. The semantics is therefore "last assignments-poll", which is the right signal for "is the agent's main loop alive?" anyway.

### 5.5 Transfer rate and ETA

For rows in `transferring` state, `transfer_rate_bps` is computed from a per-assignment in-memory sample buffer **owned by the route layer**, not by the projection function. Because `Assignment` has a composite primary key `(client_id, asset_id)` (no integer surrogate), the tracker is keyed by that tuple:

```python
# server/src/syncarr_server/services/rate_tracker.py
AssignmentKey = tuple[str, int]   # (client_id, asset_id)

class RateTracker:
    def __init__(self, max_samples: int = 8): ...
    def record(self, key: AssignmentKey, sample: RateSample) -> None: ...
    def samples_for(self, key: AssignmentKey) -> Sequence[RateSample]: ...
```

The route handler:
1. Reads samples for each `(client_id, asset_id)` from the tracker.
2. Passes them into `project()` as `rate_samples=...`.
3. The projection function computes rate/ETA when ≥2 samples spaced > 1s exist.

Sample insertion sites:
- The progress writer in `update_assignment_progress` records a `RateSample(at=now, bytes_downloaded=payload.bytes_downloaded)` after a successful strictly-increasing update.
- No other write site is necessary; samples come exclusively from the agent's progress reports.

Rate formula:
```
rate_bps = (samples[-1].bytes - samples[0].bytes) / (samples[-1].at - samples[0].at).total_seconds()
eta_seconds = (asset.size_bytes - assignment.bytes_downloaded) / rate_bps   # if rate_bps > 0
```

Stalled rows (substate `stalled`) and verifying rows (substate `verifying`) suppress rate and ETA — they are misleading near zero or after the bytes-counter saturates.

**Resilience note:** The sample buffer is in-process memory. After a server restart it is empty until two new progress events arrive; rate/ETA will be null in the meantime. With multiple uvicorn workers (not currently the case — ADR-007 is monolith) the buffer would fragment per-worker. Treat rate/ETA as **best-effort diagnostics**, not authoritative metrics.

### 5.6 Multi-profile (client, media_item) support

The DB allows a client to have two assignments for the same `media_item_id` via two different `profile_id` values (e.g., one passthrough + one h265-1080p). The taxonomy supports this naturally — the projection runs per-(asset, client) pair, so two assignments produce two independent rows.

Surface implications:
- `GET /api/queue` exposes `asset_id` + `profile_id` per row; rendered as separate rows in the Queue UI.
- `GET /api/clients/{id}/assignments` exposes `asset_id` + `profile_id` (added in §6.2). The Library pill UI is updated to render one pill per `(media_item_id, profile_id)` rather than collapsing on `media_item_id`.
- **Pill label rule:** when only one profile exists for a given `media_item_id` for that client (the common case), the pill is unlabeled (visually identical to today). When two or more profiles exist, each pill carries a small `profile_id` chip (e.g. `passthrough`, `h265-1080p`) so the operator can distinguish them. This applies only to per-episode/per-media-item pills, not to bulk pills (§7.2 below).

### 5.7 Recent confirm-error signal

When the server's confirm endpoint (`POST /assignments/{asset_id}/confirm` in `server/src/syncarr_server/routes/agent.py`, function `confirm_assignment`) rejects an agent's confirm with a checksum mismatch or size mismatch, the server records:

```sql
last_confirm_error_at         DATETIME NULL
last_confirm_error_reason     TEXT NULL    -- "checksum_mismatch" | "size_mismatch"
```

These are new nullable columns on `assignments` (§6.4). Set in the rejecting confirm path; cleared (set back to NULL) on a successful `delivered` confirm.

Pipeline projection enhancement:

- When `assignment.last_confirm_error_at IS NOT NULL` and `(now - last_confirm_error_at) ≤ confirm_error_recent_window_seconds` (default 1 hour):
  - For substate `downloading`: detail becomes `"last attempt failed: {reason} — retrying"`.
  - For substate `verifying`: detail becomes `"verifying after recent {reason}"`.
  - For substate `stalled`: detail unchanged (stalled is more actionable than recent error context).
  - For substate `agent_offline`: detail unchanged (offline wins).
- After the recent window expires, the columns remain set (historical record) but the projection no longer surfaces them.

Rationale: today, a checksum mismatch causes an aria2 retry that visually looks like a healthy download from scratch. Operators have no signal that the previous round failed. A simple "last attempt failed" tag in the detail line during the next attempt closes that loop without inventing a new substate.

Server change site is a small edit to the existing `confirm_assignment` handler — no agent change.

## 6. API

### 6.1 New endpoint: `GET /api/queue`

Returns one row per visible `(asset, client)` pair. Sorted by:
1. `pipeline_status` priority: `transferring` < `queued` < `failed` < `ready`
2. `Assignment.created_at desc` (delivery work order, not asset-creation order)

```json
{
  "rows": [
    {
      "asset_id": 14,
      "client_id": "caravan-pi",
      "media_item_id": "11261",
      "filename": "Paw Patrol - S01E28-29 ... .mkv",
      "profile_id": "passthrough",
      "size_bytes": 756891234,
      "bytes_downloaded": 220753408,
      "transfer_rate_bps": 3357184.0,
      "eta_seconds": 159.0,
      "pipeline_status": "transferring",
      "pipeline_substate": "downloading",
      "pipeline_detail": null,
      "delivered_at": null,
      "created_at": "2026-05-08T08:12:11Z"
    }
  ]
}
```

`created_at` in the response is the `Assignment.created_at`.

Query params (all optional):
- `status=queued` — repeated query param, FastAPI-native multi-value (e.g. `?status=queued&status=transferring`). Comma-separated is **not** supported.
- `client_id=<id>` — restrict to one client.

Validation: `?status=invalid` returns **422** (FastAPI/Pydantic native for `Literal` query param); the response body is the standard FastAPI validation error shape.

The endpoint returns **all visible rows by default**, including `ready`. `/api/queue` is intended as a current-state ledger (active work + completed deliveries), not just an active-work view. The UI provides the active/all toggle through the existing filter tabs. Pagination is deferred (§3); when scale demands it, default to active-only with `?include_ready=true` opt-in.

Auth: same UI bearer token as other `/api/*` endpoints (the FastAPI router mounts these under `/api`; `routes/ui.py` declares the unprefixed paths internally).

### 6.2 Modified endpoint: `GET /api/clients/{id}/assignments` (additive)

Currently returns `ClientAssignmentSchema { media_item_id, state }` (`server/src/syncarr_server/schemas.py:180`). The change is **additive** — no field is removed or renamed:

```python
class ClientAssignmentSchema(Schema):
    media_item_id: str
    state: AgentAssignmentState        # KEPT — old field, used by current UI
    asset_id: int                       # NEW — disambiguates multi-profile (§5.6)
    profile_id: str                     # NEW — disambiguates multi-profile
    pipeline_status: PipelineStatus     # NEW — driven by pipeline.project()
    pipeline_substate: PipelineSubstate # NEW
    pipeline_detail: str | None         # NEW
```

Server can deploy independently of UI: the new fields are populated; the existing UI ignores them. UI deploys later to consume the new fields and stops reading `state`. A future ticket retires `state` once no consumer remains (out of scope here).

### 6.3 Unchanged

- `GET /api/assets` — keeps `Asset.status` semantics.
- All agent endpoints (`GET /assignments`, `POST /assignments/{asset_id}/confirm`, `GET /download/{asset_id}`, `PATCH /assignments/{asset_id}/progress`, `/reconcile`).
- The `confirm_assignment` handler gains internal logic to set/clear `last_confirm_error_*` columns (§5.7); the **agent ↔ server contract is unchanged** — same request shape, same response codes.
- Auth tokens, secrets, deployment.
- Existing `clients.last_seen` semantics (poll-only writes, §5.4).

### 6.4 Schema additions

Three new columns; one Alembic migration:

```sql
ALTER TABLE assignments ADD COLUMN bytes_downloaded_updated_at DATETIME NULL;
ALTER TABLE assignments ADD COLUMN last_confirm_error_at DATETIME NULL;
ALTER TABLE assignments ADD COLUMN last_confirm_error_reason TEXT NULL;
```

No backfill needed:
- `bytes_downloaded_updated_at IS NULL` → not stalled (§5.3).
- `last_confirm_error_at IS NULL` → no recent error to surface.

`clients.last_poll_at` is **not** added — existing `clients.last_seen` is reused (§5.4).

**Server config addition** (`server/src/syncarr_server/config.py`):
```python
agent_poll_interval_seconds: int = 300
```

Mirrors the agent's default; used by §5.3 and §5.4 thresholds.

**Migration safety on SQLite WAL:** `ALTER TABLE … ADD COLUMN` with a nullable default is fast (metadata-only) and takes a brief schema-modification lock. The Swarm deploy pattern already restarts the server container per deploy (Gitea Actions stack updates); migration runs in the entrypoint's `alembic upgrade head` before the FastAPI app binds. No special operator step required beyond the existing deploy flow.

`bytes_downloaded_updated_at` is set in `update_assignment_progress` per §5.3 rules. `last_confirm_error_at` and `last_confirm_error_reason` are set/cleared in `confirm_assignment` per §5.7.

## 7. UI

### 7.1 QueueScreen — `ui/src/screens/QueueScreen.tsx`

- Data source switches from `getAllAssets()` to `getQueue()` (new API client).
- Row identity becomes per-(asset, client). Each row carries a `to {client_id}` chip, always visible (no special-case for single-client today; stable as the system grows). The existing UI already uses an arrow glyph in similar chips; this is a render-time decision.
- Badge driven by `row.pipeline_status`. Color mapping:
  - `queued` → existing "queued" treatment
  - `transferring` → existing "transcoding" amber treatment
  - `ready` → existing "ready" green
  - `failed` → existing "failed" red
- Secondary detail line under the filename, small muted text, rendered when `row.pipeline_detail` is non-null.
- Progress bar appears only when `pipeline_status === "transferring"`. Determinate when `0 < bytes < size`; indeterminate pulse when `substate === "verifying"`.
- A new metrics line beside `"X MB / Y MB"`: `"3.2 MB/s · ETA 2m 39s"` when both `transfer_rate_bps` and `eta_seconds` are non-null. Hidden when `substate === "stalled"` or `verifying`.
- Filter tabs: `All / Queued / Transferring / Ready / Failed`. Counts derived client-side from rendered rows.
- Show-name grouping (the existing `parseShowGroup` parser) is preserved.

### 7.2 LibraryScreen pills — `ui/src/screens/LibraryScreen.tsx` and components

There are two pill kinds with different aggregation rules:

**Per-episode / per-media-item pills** consume the new `pipeline_status` field on `GET /api/clients/{id}/assignments` directly. One pill per `(media_item_id, profile_id)` for that client. Multi-profile labels per §5.6.

**`BulkSyncPill`** (show-level or season-level subscription that fans out to many episode assignments) **aggregates** the children's `pipeline_status` values:

```
if any child is `failed`        → bulk pill is `failed`
elif any child is `transferring`→ bulk pill is `transferring`
elif any child is `queued`      → bulk pill is `queued`
else (all delivered)            → bulk pill is `ready`
```

Rationale: operator wants the worst-of state — a "ready" bulk pill should mean "every child arrived". Failed wins so a single failed transcode is not invisible behind otherwise healthy queued/transferring siblings.

Pill color mapping:

| pipeline_status | pill |
|---|---|
| `queued` | grey "queued" |
| `transferring` | amber "syncing" (reuses transcoding token) |
| `ready` | green "ready" |
| `failed` | red "failed" |

Pills do not render the secondary detail line, progress bar, transfer rate, or ETA — they remain compact summary surfaces. Click-through to the Queue is a future enhancement; out of scope here.

### 7.3 Polling

Unchanged: 15s `refetchInterval` on QueueScreen; existing cadence on LibraryScreen.

## 8. Testing

### 8.1 Unit tests — `server/tests/test_pipeline.py` (new)

One test per row of the §5.2 truth table, plus:

- Stalled threshold edges (1 second under, 1 second over).
- Agent-offline threshold edges.
- Transfer rate with 0/1/2/8 samples; null when fewer than 2 samples spaced > 1s.
- Substate `stalled` suppresses transfer_rate and eta (both null).
- Substate `verifying` suppresses transfer_rate and eta (bytes ≥ size case).
- ETA when rate is zero or null returns null.
- Row 7 (offline) takes precedence over rows 8–11 (verified for each).
- Row 3 (delivered) takes precedence over row 4 (asset failed). Test the explicit case: `assignment.state="delivered"` AND `asset.status="failed"` → `pipeline_status="ready"`.
- Invalid byte cases: null `asset.size_bytes` (→ row 8); `bytes_downloaded < 0` (treated as 0); `bytes_downloaded > asset.size_bytes` (→ row 10 verifying); null `bytes_downloaded` (→ row 9).
- `assignment is None` → `visible=False`.
- Evict precedence: `state="evict"` AND `evict_requested_at IS NULL` → invisible (and vice versa).
- Detail-string differentiation: passthrough (sha256=None) `verifying` says "verifying size"; transcoded says "verifying checksum".
- §5.7 confirm-error enhancement:
  - `last_confirm_error_at` set, within window, substate `downloading` → detail "last attempt failed: checksum_mismatch — retrying".
  - `last_confirm_error_at` set, within window, substate `verifying` → detail "verifying after recent checksum_mismatch".
  - `last_confirm_error_at` set, within window, substate `stalled` → detail unchanged (stalled wins).
  - `last_confirm_error_at` set, outside window → detail null, behaves as if column were null.
  - `last_confirm_error_at` IS NULL → behaves as base table.
- RateTracker keying: two samples for `(clientA, asset1)` and one for `(clientB, asset1)` are isolated; rate computed only over the matching tuple.

### 8.2 Integration tests — `server/tests/test_routes_queue.py` (new)

Black-box against FastAPI test client + temp SQLite:

- Empty DB returns `{"rows": []}`.
- Asset+assignment fixtures for each pipeline_status value.
- Two assignments on one asset → two rows, distinct `client_id`, independent statuses.
- Two assignments on different profiles for same media_item, same client → two rows with distinct `profile_id`.
- Evicted/evicting assignments excluded.
- `?status=queued` (single value), `?status=queued&status=transferring` (repeated).
- `?status=invalid` returns **422** with FastAPI's standard validation-error shape.
- `?client_id=…` filter.
- Sort priority assertion (and ties broken by `Assignment.created_at desc`).
- Auth: missing/wrong bearer → 401.

Plus integration tests for `GET /api/clients/{id}/assignments`:
- New fields populated correctly.
- Old `state` field still present for backwards compatibility.
- Multi-profile case returns two rows, not one.

**Cross-projection regression:** assert that `GET /api/clients/{id}/assignments` returns the same `pipeline_status` for the same fixture as `GET /api/queue` does — guarantees both surfaces share the projection.

### 8.3 Progress-writer tests — extend `server/tests/test_routes_agent.py`

Cover §5.3 update rules for `update_assignment_progress`. Each test asserts both the stored value AND the timestamp:

- Strictly increasing `bytes_downloaded` → updates value AND advances `bytes_downloaded_updated_at`.
- Equal `bytes_downloaded` (heartbeat) → no change to value, **no advance** of timestamp.
- Decreasing `bytes_downloaded` → value not lowered, timestamp not advanced; warning logged.
- Negative `bytes_downloaded` → 422 (Pydantic constraint).
- `bytes_downloaded > asset.size_bytes` → accepted; subsequent projection treats as verifying.
- A second strictly-increasing post produces a second `RateSample` in the tracker.

Plus new tests for §5.7 in `confirm_assignment`:

- Confirm with checksum mismatch → assignment not delivered; `last_confirm_error_at = now`; `last_confirm_error_reason = "checksum_mismatch"`.
- Confirm with size mismatch (passthrough) → same shape; reason `"size_mismatch"`.
- Confirm successful → `last_confirm_error_at` and `last_confirm_error_reason` cleared to NULL (regardless of prior state).

### 8.4 UI tests — `ui/src/screens/QueueScreen.test.tsx` (new or extended) and pill tests

- Renders one row per (asset, client) from a fixture.
- Badge color per `pipeline_status`.
- Progress bar present iff `transferring`; determinate when bytes < size; indeterminate when `verifying`.
- `pipeline_detail` rendered when non-null.
- Transfer rate + ETA rendered when both non-null; hidden when `stalled` or `verifying`.
- Filter tabs filter rows; counts in subtitle update.
- Multi-client fixture renders two rows with distinct chips.
- Pill component fixture-driven test for each `pipeline_status` → pill color.
- Multi-profile per-episode pill fixture renders two pills for one media_item, each with a profile_id chip; single-profile fixture renders one unlabeled pill.
- `BulkSyncPill` aggregation:
  - mixed children with one `failed` → bulk pill is `failed`.
  - mixed children with one `transferring`, rest `queued`/`ready` → bulk pill is `transferring`.
  - mixed children with one `queued`, rest `ready` → bulk pill is `queued`.
  - all `ready` children → bulk pill is `ready`.

### 8.5 Manual verification (post-implementation)

Carried in the implementation plan, not in this design spec.

## 9. Migration / rollout

- Single Alembic migration adds `assignments.bytes_downloaded_updated_at`, `assignments.last_confirm_error_at`, `assignments.last_confirm_error_reason`. Runs in container entrypoint per existing deploy flow.
- Server config adds `agent_poll_interval_seconds` (default 300); deploy is a no-op if the env var is not set.
- Server deployable independently of UI thanks to additive API changes (§6.2).
- UI deploys later, swaps QueueScreen + LibraryScreen pill data sources, stops consuming `state`.
- A follow-up cleanup ticket retires the `state` field on `ClientAssignmentSchema` once no consumer reads it (out of scope here).
- No agent change required; agent ↔ server contract is untouched.

## 10. Operational notes

- `transfer_rate_bps` and `eta_seconds` are best-effort diagnostics. They will be null:
  - For ~30s after server restart (sample buffer empty until ≥2 progress reports arrive).
  - For stalled or verifying rows (deliberately suppressed).
  - When fewer than 2 samples are available.
- `bytes_downloaded_updated_at` writes happen on every progress poll where bytes increase. For a busy multi-asset transfer this is a few writes/minute on caravan-pi — well within ADR-009's single-writer SQLite envelope.
- The Queue endpoint returns all visible rows including `ready`. With current scale (≤ a few hundred assets) this is fine; if/when scale dictates, add `?include_ready=false` default.
- `last_confirm_error_*` columns persist after the recent-window expires (historical record). They are cleared on a successful delivered confirm, so steady-state delivered assignments never carry stale error data.

## 11. Open questions

- **Failed taxonomy growth.** Today only `transcode_failed` produces `pipeline_status = failed`. When download retry budgets land (post-MVP), a `download_failed` substate joins the table. The spec is forward-compatible but the threshold at which transient retries flip to terminal failure is undecided.
- **Library pill click-through to Queue.** Tempting UX win but not in scope.
- **Filter persistence.** Should the active filter tab survive page refresh? Existing screen does not persist; this spec preserves that.
- **Server-side cache file drift.** Mentioned in §3 as out of scope; warrants its own ticket eventually.
- **Clients-screen evicting visibility.** §4 says decommissioning progress lives on the Clients screen, but `ClientSchema` currently exposes only `decommissioning: bool`. Whether to add a `pending_evictions: int` (or richer breakdown) is a follow-up; this spec's scope is Queue + Library projection, not the Clients-screen redesign.

## 12. Acceptance criteria

The change is complete when:

1. `pytest server/tests/test_pipeline.py` covers every row of the §5.2 table, plus all §8.1 edges, and passes.
2. `pytest server/tests/test_routes_queue.py` covers the §8.2 list and passes.
3. `pytest server/tests/test_routes_agent.py` covers the §8.3 progress-writer + confirm-error rules and passes.
4. `npm test` covers §8.4 list and passes.
5. The manual verification checklist (carried in the implementation plan) is walked end-to-end against caravan-pi and recorded in `Obsidian/Lab/syncarr/learnings-2026-05-08-pipeline-status.md`.
6. No row in the production Queue UI displays `ready` while `bytes_downloaded < size_bytes` for that row's assignment.
7. No code path outside `pipeline.project()` **decides or derives** a row's `pipeline_status`. (Endpoint definitions, OpenAPI literals, UI types, and filter parsers necessarily *mention* the enum; they do not compute it.)
