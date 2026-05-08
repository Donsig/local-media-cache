# Pipeline Status Redesign — Design Spec

- Created: 2026-05-08
- Revision: v2 (post-Codex-review)
- Status: Draft, awaiting second Codex review
- Author: brainstorm session (Claude Code) with user @anders
- Scope: server (`server/`) + UI (`ui/`); no agent changes
- Related: bug #28 (UI pill accuracy, fixed 2026-05-08), bug #34 (etag/abs URL, separate ticket)

## Revision history

- **v1 (2026-05-08, commit `5f129c0`)** — initial draft.
- **v2 (this revision)** — addresses Codex review (review file: `2026-05-08-pipeline-status-redesign-review-codex.md`). Changes:
  - Use the real persisted state value `"evict"` (not `"evicted"`).
  - Reorder truth table: `delivered → ready` precedes `asset.status=failed`.
  - Replace proposed `clients.last_poll_at` column with the existing `clients.last_seen`.
  - Reference the actual progress writer: `PATCH /assignments/{asset_id}/progress`.
  - Make API field additions on `GET /api/clients/{id}/assignments` *additive*, not breaking; keep `state` field, add `pipeline_status` + `pipeline_substate` + `asset_id` + `profile_id`.
  - Restore `project()` purity by passing rate samples as an explicit input.
  - Add explicit truth-table rows for `assignment is None`, `size_bytes is None`, negative/over-size byte values.
  - Fold operator-facing decisions from Codex's QUESTIONS (delivered terminality, evicting visibility, multi-profile support, last_seen write surface, ledger-vs-active-work intent) into the design.
  - Use `queued` (not `queue`) for the primary status to match existing vocab.

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
4. Surface two operator-relevant pipeline conditions that today silently masquerade as healthy: **stalled transfers** and **agent offline**.
5. Surface **transfer rate + ETA** during active transfers so operators can decide whether to wait or switch network (Starlink ↔ 4G).
6. API field changes on existing endpoints are **additive**; old fields stay until a separate cleanup ticket retires them.

## 3. Non-Goals

- No DB schema changes — existing `clients.last_seen` and `assignments.bytes_downloaded` are reused; one new column (§6.4) for transfer-stalled detection.
- No agent changes. Agent ↔ server contract is unchanged.
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

Rows where the assignment has `evict_requested_at IS NOT NULL` or `state == "evict"` are **invisible** to Queue and Library pill surfaces. Decommissioning and subscription-deletion progress remains visible on the **Clients screen** (`GET /api/clients`), where deletion-in-flight is the natural concern. This is a deliberate split: the Queue is about active and completed *delivery*; eviction is about *removal*.

A secondary `pipeline_substate` enum carries finer detail. The UI uses it for styling and as the source for the human-readable detail line:

| Substate | Parent status | Detail string (example) |
|---|---|---|
| `transcoding_pending` | `queued` | "waiting to transcode" |
| `transcoding` | `queued` | "transcoding on server" |
| `waiting_for_agent` | `queued` | "waiting for agent to pick up" |
| `agent_offline` | `queued` | "agent offline (last seen 1h ago)" |
| `downloading` | `transferring` | null (progress bar carries info) |
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
) -> PipelineProjection: ...
```

Properties:

- **Pure.** No DB writes, no network, no filesystem. Deterministic for `(asset, assignment, client, now, poll_interval_seconds, rate_samples)`. Rate samples are an explicit input so the function stays referentially transparent (Codex review B4).
- **Total.** Returns a value for every combination of inputs; never raises on valid ORM state.
- **Single source of truth.** Both `GET /api/queue` and `GET /api/clients/{id}/assignments` use this function.
- **Fully unit-tested** with one test per row of the truth table (§5.2).

Time-dependent decisions (stalled, agent_offline) take `now` as a parameter so tests can pin time without monkey-patching.

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
| 9 | `bytes_downloaded` is null or `≤ 0` | `queued` | `waiting_for_agent` | "waiting for agent to pick up" |
| 10 | `bytes_downloaded ≥ size_bytes` | `transferring` | `verifying` | detail string per §4 (checksum vs size) |
| 11 | `0 < bytes_downloaded < size_bytes` AND **stalled** (see §5.3) | `transferring` | `stalled` | "stalled — no progress in Xm" |
| 12 | `0 < bytes_downloaded < size_bytes` (otherwise) | `transferring` | `downloading` | null |

Precedence rationale:

- **Row 3 before 4** — once a file is on the satellite, transcode failure on the server cannot unwrite it. The operator's correct mental model is "this delivery is done".
- **Row 7 before 8–11** — when the agent isn't polling, "stalled" or "downloading" is misleading; the actionable signal is "go check the satellite". Stalled, in-progress, and pending all collapse into `agent_offline`.
- **Invalid byte combinations** — `bytes_downloaded < 0` is treated as 0 (defensive; should never happen). `bytes_downloaded > size_bytes` is clamped to size for the row 10 check (treated as verifying). Negative size is impossible (DB constraint). These are documented in tests.

### 5.3 Stalled detection

A transfer is **stalled** iff:

```
bytes_downloaded > 0
  AND assignment.bytes_downloaded_updated_at IS NOT NULL
  AND now - assignment.bytes_downloaded_updated_at > max(2 * poll_interval_seconds, 120s)
```

`bytes_downloaded_updated_at` is a new nullable column on `assignments` (§6.4). Null means "no progress timestamp yet" — explicitly **not stalled** (could be a freshly-migrated row or a transfer that hasn't reported yet).

The threshold is `max(2 × poll_interval, 120s)` so a single missed poll doesn't trigger stalled.

**Update rules** for `bytes_downloaded_updated_at` (writer path: `update_assignment_progress()` in `server/src/syncarr_server/routes/agent.py:225`):

- Set timestamp to `now` whenever `payload.bytes_downloaded > assignment.bytes_downloaded`.
- Do **not** bump on equal values (that's the heartbeat case — useful elsewhere but not for stall detection).
- Do **not** bump on lower values (clamp to existing; log a warning).
- Do **not** bump on negative values (reject with 400; agent should never send this).
- For `payload.bytes_downloaded > assignment.size_bytes` (over-size): accept and store, treat as verifying. Log at INFO (this can happen when aria2's reported total includes overhead).

These are server-side validations; agent code unchanged.

### 5.4 Agent offline detection

A client is offline iff:

```
client.last_seen IS NULL
  OR (now - client.last_seen) > max(3 * poll_interval_seconds, 180s)
```

Reuses the existing `clients.last_seen` column (`server/src/syncarr_server/models.py:22`); **no new column needed** (Codex review B7).

`last_seen` is currently bumped on `GET /assignments` poll (`server/src/syncarr_server/routes/agent.py`). This spec **does not widen** that write surface — `last_seen` continues to be poll-only, not bumped on `/download`, `/progress`, or `/confirm`. Rationale (per ADR-009 single-writer SQLite): Range-retry storms on `/download` would otherwise turn file serving into DB writes; limiting `last_seen` writes to the polling endpoint keeps contention bounded. The semantics is therefore "last assignments-poll", which is the right signal for "is the agent's main loop alive?" anyway.

### 5.5 Transfer rate and ETA

For rows in `transferring` state, `transfer_rate_bps` is computed from a per-assignment in-memory sample buffer **owned by the route layer**, not by the projection function:

```python
# server/src/syncarr_server/pipeline.py
@dataclass(frozen=True)
class RateSample:
    at: datetime
    bytes_downloaded: int

# Buffer lives in a route-layer service (not in pipeline.py):
# server/src/syncarr_server/services/rate_tracker.py
class RateTracker:
    def __init__(self, max_samples: int = 8): ...
    def record(self, assignment_id: int, sample: RateSample) -> None: ...
    def samples_for(self, assignment_id: int) -> Sequence[RateSample]: ...
```

The route handler:
1. Reads samples for each assignment from the tracker.
2. Passes them into `project()` as `rate_samples=...`.
3. The projection function computes rate/ETA when ≥2 samples spaced > 1s exist.

Rate formula:
```
rate_bps = (samples[-1].bytes - samples[0].bytes) / (samples[-1].at - samples[0].at).total_seconds()
eta_seconds = (size_bytes - bytes_downloaded) / rate_bps   # if rate_bps > 0
```

Stalled rows (substate `stalled`) suppress rate and ETA — they are misleading near zero.

**Resilience note:** The sample buffer is in-process memory. After a server restart it is empty until two new progress events arrive; rate/ETA will be null in the meantime. With multiple uvicorn workers (not currently the case — ADR-007 is monolith) the buffer would fragment per-worker. Treat rate/ETA as **best-effort diagnostics**, not authoritative metrics. Documented as such in §10.

### 5.6 Multi-profile (client, media_item) support

The DB allows a client to have two assignments for the same `media_item_id` via two different `profile_id` values (e.g., one passthrough + one h265-1080p). The taxonomy supports this naturally — the projection runs per-(asset, client) pair, so two assignments produce two independent rows.

Surface implications:
- `GET /api/queue` exposes `asset_id` + `profile_id` per row; rendered as separate rows in the Queue UI.
- `GET /api/clients/{id}/assignments` exposes `asset_id` + `profile_id` (added in §6.2). The Library pill UI is updated to render one pill per `(media_item_id, profile_id)` rather than collapsing on `media_item_id`. If only one profile is subscribed (the common case), this looks identical to today.

## 6. API

### 6.1 New endpoint: `GET /api/queue`

Returns one row per visible `(asset, client)` pair. Sorted by:
1. `pipeline_status` priority: `transferring` < `queued` < `failed` < `ready`
2. `created_at desc`

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

Query params (all optional):
- `status=queued` — repeated query param, FastAPI-native multi-value (e.g. `?status=queued&status=transferring`). Comma-separated is **not** supported (avoids custom parsing per Codex review I8).
- `client_id=<id>` — restrict to one client.

The endpoint returns **all visible rows by default**, including `ready`. `/api/queue` is intended as a current-state ledger (active work + completed deliveries), not just an active-work view. The UI provides the active/all toggle through the existing filter tabs. Pagination is deferred (§3); when scale demands it, default to active-only with `?include_ready=true` opt-in.

Auth: same UI bearer token as other `/api/*` endpoints (the FastAPI router mounts these under `/api`; `routes/ui.py` declares the unprefixed paths internally).

### 6.2 Modified endpoint: `GET /api/clients/{id}/assignments` (additive)

Currently returns `ClientAssignmentSchema { media_item_id, state }` (`server/src/syncarr_server/schemas.py:180`). The change is **additive** — no field is removed or renamed (Codex review B5):

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

Library pill consumers update their identity from `media_item_id` to `(media_item_id, profile_id)` to handle multi-profile.

### 6.3 Unchanged

- `GET /api/assets` — keeps `Asset.status` semantics.
- All agent endpoints (`GET /assignments`, `POST /confirm`, `POST /download`, `PATCH /assignments/{asset_id}/progress`, `/reconcile`).
- Auth tokens, secrets, deployment.
- Existing `clients.last_seen` semantics (poll-only writes, §5.4).

### 6.4 Schema additions

One new column; one Alembic migration:

```sql
ALTER TABLE assignments ADD COLUMN bytes_downloaded_updated_at DATETIME NULL;
```

No backfill needed — null maps cleanly to "no progress timestamp yet" → not stalled.

`clients.last_poll_at` is **not** added — existing `clients.last_seen` is reused (§5.4).

**Migration safety on SQLite WAL:** `ALTER TABLE … ADD COLUMN` with a nullable default is fast (metadata-only) and takes a brief schema-modification lock. The Swarm deploy pattern already restarts the server container per deploy (Gitea Actions stack updates); migration runs in the entrypoint's `alembic upgrade head` before the FastAPI app binds. No special operator step required beyond the existing deploy flow.

`bytes_downloaded_updated_at` is set in `update_assignment_progress()` per §5.3 rules (single writer; aligns with ADR-009).

## 7. UI

### 7.1 QueueScreen — `ui/src/screens/QueueScreen.tsx`

- Data source switches from `getAllAssets()` to `getQueue()` (new API client).
- Row identity becomes per-(asset, client). Each row carries a `→ {client_id}` chip, always visible (no special-case for single-client today; stable as the system grows).
- Badge driven by `row.pipeline_status`. Color mapping:
  - `queued` → existing "queued" treatment
  - `transferring` → existing "transcoding" amber treatment
  - `ready` → existing "ready" green
  - `failed` → existing "failed" red
- Secondary detail line under the filename, small muted text, rendered when `row.pipeline_detail` is non-null.
- Progress bar appears only when `pipeline_status === "transferring"`. Determinate when `0 < bytes < size`; indeterminate pulse when `substate === "verifying"`.
- A new metrics line beside `"X MB / Y MB"`: `"3.2 MB/s · ETA 2m 39s"` when both `transfer_rate_bps` and `eta_seconds` are non-null. Hidden when `substate === "stalled"`.
- Filter tabs: `All / Queued / Transferring / Ready / Failed`. Counts derived client-side from rendered rows.
- Show-name grouping (the existing `parseShowGroup` parser) is preserved.

### 7.2 LibraryScreen pills — `ui/src/screens/LibraryScreen.tsx` and components

The `BulkSyncPill` and per-episode pills consume the new `pipeline_status` field on `GET /api/clients/{id}/assignments`. Mapping:

| pipeline_status | pill |
|---|---|
| `queued` | grey "queued" |
| `transferring` | amber "syncing" (reuses transcoding token) |
| `ready` | green "ready" |
| `failed` | red "failed" |

Pills do not render the secondary detail line, progress bar, transfer rate, or ETA — they remain compact summary surfaces. Click-through to the Queue is a future enhancement; out of scope here.

Pills key on `(media_item_id, profile_id)` so a client subscribed to two profiles for the same media item shows two pills (rare today; supported by design per §5.6).

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
- Invalid byte cases: null `size_bytes` (→ row 8); `bytes_downloaded < 0` (treated as 0); `bytes_downloaded > size_bytes` (→ row 10 verifying); null `bytes_downloaded` (→ row 9).
- `assignment is None` → `visible=False`.
- Evict precedence: `state="evict"` AND `evict_requested_at IS NULL` → invisible (and vice versa).
- Detail-string differentiation: passthrough (sha256=None) `verifying` says "verifying size"; transcoded says "verifying checksum".

### 8.2 Integration tests — `server/tests/test_routes_queue.py` (new)

Black-box against FastAPI test client + temp SQLite:

- Empty DB returns `{"rows": []}`.
- Asset+assignment fixtures for each pipeline_status value.
- Two assignments on one asset → two rows, distinct `client_id`, independent statuses.
- Two assignments on different profiles for same media_item, same client → two rows with distinct `profile_id`.
- Evicted/evicting assignments excluded.
- `?status=queued` (single value), `?status=queued&status=transferring` (repeated).
- `?status=invalid` returns 400 or 422 with a clear validation error.
- `?client_id=…` filter.
- Sort priority assertion.
- Auth: missing/wrong bearer → 401.

Plus integration tests for `GET /api/clients/{id}/assignments`:
- New fields populated correctly.
- Old `state` field still present for backwards compatibility.
- Multi-profile case returns two rows, not one.

**Cross-projection regression:** assert that `GET /api/clients/{id}/assignments` returns the same `pipeline_status` for the same fixture as `GET /api/queue` does — guarantees both surfaces share the projection.

### 8.3 Progress-writer tests — `server/tests/test_routes_agent.py` (extend)

Cover §5.3 update rules for `update_assignment_progress()`:

- `bytes_downloaded > previous` → updates value AND `bytes_downloaded_updated_at`.
- `bytes_downloaded == previous` → no timestamp bump (heartbeat-only).
- `bytes_downloaded < previous` → stored value not lowered; warning logged.
- `bytes_downloaded < 0` → 400.
- `bytes_downloaded > size_bytes` → accepted; subsequent projection treats as verifying.
- Concurrent identical posts → idempotent (last write wins on value; timestamp reflects last bump).

### 8.4 UI tests — `ui/src/screens/QueueScreen.test.tsx` (new or extended)

- Renders one row per (asset, client) from a fixture.
- Badge color per `pipeline_status`.
- Progress bar present iff `transferring`; determinate when bytes < size; indeterminate when `verifying`.
- `pipeline_detail` rendered when non-null.
- Transfer rate + ETA rendered when both non-null; hidden when `stalled`.
- Filter tabs filter rows; counts in subtitle update.
- Multi-client fixture renders two rows with distinct chips.
- Pill component fixture-driven test for each `pipeline_status` → pill color.
- Multi-profile pill fixture renders two pills for one media_item.

### 8.5 Manual verification (post-implementation)

Walked against `docker-host01` + `caravan-pi`. List belongs in the implementation plan, not this spec — see plan generated by the `writing-plans` skill.

## 9. Migration / rollout

- Single Alembic migration adds `assignments.bytes_downloaded_updated_at`. Runs in container entrypoint per existing deploy flow.
- Server deployable independently of UI thanks to additive API changes (§6.2).
- UI deploys later, swaps QueueScreen + LibraryScreen pill data sources, stops consuming `state`.
- A follow-up cleanup ticket retires the `state` field on `ClientAssignmentSchema` once no consumer reads it (out of scope here).
- No agent change required; agent ↔ server contract is untouched.

## 10. Operational notes

- `transfer_rate_bps` and `eta_seconds` are best-effort diagnostics. They will be null:
  - For ~30s after server restart (sample buffer empty until ≥2 progress reports arrive).
  - For stalled rows (deliberately suppressed).
  - When fewer than 2 samples are available.
- `bytes_downloaded_updated_at` writes happen on every progress poll where bytes increase. For a busy multi-asset transfer this is a few writes/minute on caravan-pi — well within ADR-009's single-writer SQLite envelope.
- The Queue endpoint returns all visible rows including `ready`. With current scale (≤ a few hundred assets) this is fine; if/when scale dictates, add `?include_ready=false` default.

## 11. Open questions

- **Failed taxonomy growth.** Today only `transcode_failed` produces `pipeline_status = failed`. When download retry budgets land (post-MVP), a `download_failed` substate joins the table. The spec is forward-compatible but the threshold at which transient retries flip to terminal failure is undecided.
- **Library pill click-through to Queue.** Tempting UX win but not in scope.
- **Filter persistence.** Should the active filter tab survive page refresh? Existing screen does not persist; this spec preserves that.
- **Server-side cache file drift.** Mentioned in §3 as out of scope; warrants its own ticket eventually.

## 12. Acceptance criteria

The change is complete when:

1. `pytest server/tests/test_pipeline.py` covers every row of the §5.2 table, plus all §8.1 edges, and passes.
2. `pytest server/tests/test_routes_queue.py` covers the §8.2 list and passes.
3. `pytest server/tests/test_routes_agent.py` covers the §8.3 progress-writer rules and passes.
4. `npm test` covers §8.4 list and passes.
5. The manual verification checklist (carried in the implementation plan) is walked end-to-end against caravan-pi and recorded in `Obsidian/Lab/syncarr/learnings-2026-05-08-pipeline-status.md`.
6. No row in the production Queue UI displays `ready` while `bytes_downloaded < size_bytes` for that row's assignment.
7. No code path outside `pipeline.project()` **decides or derives** a row's `pipeline_status`. (Endpoint definitions, OpenAPI literals, UI types, and filter parsers necessarily *mention* the enum; they do not compute it.)
