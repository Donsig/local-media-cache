# Pipeline Status Redesign — Design Spec

- Created: 2026-05-08
- Status: Draft, awaiting Codex review
- Author: brainstorm session (Claude Code) with user @anders
- Scope: server (`server/`) + UI (`ui/`); no agent changes
- Related: bug #28 (UI pill accuracy, fixed 2026-05-08), bug #34 (etag/abs URL, separate ticket)

## 1. Problem

The Queue UI lies about file delivery state. Today every row's badge is driven by `Asset.status`, which represents only the *server-side cache* lifecycle (`queued → transcoding → ready → failed`). Once the server has transcoded (or passthrough'd) a file into its cache, the asset becomes `ready` — even though the file may not have been picked up by the satellite, may be 30% downloaded, or may have failed mid-transfer.

In production this surfaces as `[ready]` badges next to progress bars showing 30% complete (verified by user screenshot 2026-05-08). Operators have repeatedly seen "Ready" while the file isn't on the satellite disk.

The root cause is a two-axis problem collapsed into one label:

- **Server cache axis** — `Asset.status` (`queued | transcoding | ready | failed`)
- **Per-client delivery axis** — `Assignment.state` (`pending | delivered | evicted`) plus `Assignment.bytes_downloaded`, `Assignment.evict_requested_at`

The UI projects only the first axis onto the badge. This spec replaces that with a single projection over both axes.

A previous narrower fix (bug #28, commit `b147e2e`) corrected the *Library pill* by mapping `pending+asset_ready → "queued"` in `list_client_assignments`. That logic is now duplicated and inconsistent with the Queue surface. This spec consolidates both surfaces behind one server-side projection.

## 2. Goals

1. Queue rows reflect the **delivery pipeline** (server → wire → satellite disk), not the server cache lifecycle.
2. A row marked `Ready` corresponds to an `Assignment` in `delivered` state, i.e. the agent has SHA-verified the file on disk and POSTed `confirm`.
3. Library pills and Queue rows render via a **single** server-side projection function — eliminating the bug class that produced #28.
4. Surface two operator-relevant pipeline conditions that today silently masquerade as healthy: **stalled transfers** and **agent offline**.
5. Surface **transfer rate + ETA** during active transfers so operators can decide whether to wait or switch network (Starlink ↔ 4G).

## 3. Non-Goals

- No DB schema changes beyond two timestamp columns (see §6.4).
- No agent changes. Agent ↔ server contract is unchanged.
- No changes to `GET /api/assets` semantics — it remains the per-asset cache view used by LibraryScreen tree rows.
- No new color tokens; reuse existing badge colors.
- Aria2 backpressure visibility (active vs waiting GIDs) is **deferred** to backlog; requires an agent contract change.
- Real-time stale-Ready detection (file deleted on disk between confirms) — `/reconcile` already covers this on agent restart + 24h cycle.
- Bug #37 (DELETE /assets wiping subscriptions) — separate ticket.

## 4. Status Taxonomy

The four primary `pipeline_status` values:

| Status | Meaning | Trigger |
|---|---|---|
| `queue` | The file is not yet being actively transferred to this client. | Asset is `queued`/`transcoding`, OR asset `ready` but agent hasn't begun pulling, OR agent offline. |
| `transferring` | Aria2 has the file in flight (or is verifying it post-download). | Asset `ready`, assignment `pending`, `bytes_downloaded > 0`. |
| `ready` | The file is verified on the satellite's disk. | Assignment `state = delivered`. |
| `failed` | A terminal failure operator must address. | Asset `status = failed` (transcode); or future: download retry budget exhausted. |

Rows where the assignment has `evict_requested_at` set, or `state = evicted`, are **invisible** to the queue and the library pill (they do not appear).

A secondary `pipeline_substate` enum carries finer detail (used by the UI for styling and as the source for the human-readable detail line):

| Substate | Parent status | Detail string (example) |
|---|---|---|
| `transcoding_pending` | `queue` | "waiting to transcode" |
| `transcoding` | `queue` | "transcoding on server" |
| `waiting_for_agent` | `queue` | "waiting for agent to pick up" |
| `agent_offline` | `queue` | "agent offline (last seen 1h ago)" |
| `downloading` | `transferring` | null (progress bar carries info) |
| `verifying` | `transferring` | "verifying checksum" |
| `stalled` | `transferring` | "stalled — no progress in 8m" |
| `delivered` | `ready` | null |
| `transcode_failed` | `failed` | "transcode failed: ffmpeg exit 1" |

## 5. Architecture

### 5.1 Single projection function

A new module `server/src/syncarr_server/pipeline.py` exports one pure function:

```python
PipelineStatus = Literal["queue", "transferring", "ready", "failed"]
PipelineSubstate = Literal[
    "transcoding_pending", "transcoding", "waiting_for_agent",
    "agent_offline", "downloading", "verifying", "stalled",
    "delivered", "transcode_failed",
]

@dataclass(frozen=True)
class PipelineProjection:
    visible: bool
    status: PipelineStatus | None      # None when visible=False
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
) -> PipelineProjection: ...
```

Properties:

- **Pure.** No DB writes, no network. Deterministic for `(asset, assignment, client, now)`.
- **Total.** Returns a value for every combination; never raises on valid ORM state.
- **Single source of truth.** Both `GET /api/queue` and `GET /api/clients/{id}/assignments` use this function.
- **Fully unit-tested** with one test per row of the truth table (§5.2).

Time-dependent decisions (stalled, agent_offline) take `now` as a parameter so tests can pin time without monkey-patching.

### 5.2 Decision truth table

Evaluated top to bottom; first matching row wins.

| # | asset.status | assignment.state | evict_requested_at | bytes vs size | other | → status | → substate | → detail |
|---|---|---|---|---|---|---|---|---|
| 1 | any | `evicted` | – | – | – | – | – | `visible=False` |
| 2 | any | any | not null | – | – | – | – | `visible=False` |
| 3 | `failed` | any | null | – | – | `failed` | `transcode_failed` | "transcode failed: …" |
| 4 | `queued` | any | null | – | – | `queue` | `transcoding_pending` | "waiting to transcode" |
| 5 | `transcoding` | any | null | – | – | `queue` | `transcoding` | "transcoding on server" |
| 6 | `ready` | `delivered` | null | – | – | `ready` | `delivered` | null |
| 7 | `ready` | `pending` | null | – | client offline | `queue` | `agent_offline` | "agent offline (last seen Xm ago)" |
| 8 | `ready` | `pending` | null | bytes is null or 0 | client online | `queue` | `waiting_for_agent` | "waiting for agent to pick up" |
| 9 | `ready` | `pending` | null | bytes ≥ size | – | `transferring` | `verifying` | "verifying checksum" |
| 10 | `ready` | `pending` | null | 0 < bytes < size | bytes stalled | `transferring` | `stalled` | "stalled — no progress in Xm" |
| 11 | `ready` | `pending` | null | 0 < bytes < size | otherwise | `transferring` | `downloading` | null |

Row 7 takes precedence over row 8 when the client is offline (substate carries the more actionable signal).

### 5.3 Stalled detection

A transfer is stalled iff:

```
now - assignment.bytes_downloaded_updated_at > max(2 * poll_interval_seconds, 120)
```

`bytes_downloaded_updated_at` is a new column on `assignments` (§6.4). The agent does not need to know about it; the server updates it whenever `bytes_downloaded` increases on a confirm-style POST.

The threshold is `max(2 × poll_interval, 120s)` so a single missed poll doesn't trigger stalled.

### 5.4 Agent offline detection

A client is offline iff:

```
client.last_poll_at is null OR (now - client.last_poll_at) > max(3 * poll_interval_seconds, 180)
```

`last_poll_at` is a new column on `clients` (§6.4). Updated by every authenticated agent request.

### 5.5 Transfer rate and ETA

For rows in `transferring` state, `transfer_rate_bps` is computed from a small in-memory sample buffer per assignment, kept by the server:

```
samples: Deque[(timestamp, bytes_downloaded)]   # bounded length 8
rate = (bytes[-1] - bytes[0]) / (t[-1] - t[0])  # if buffer has ≥2 samples spaced > 1s
```

The buffer lives in process memory keyed by `assignment_id`; on server restart it is empty until the next two polls populate it. `transfer_rate_bps = null` when fewer than two samples are available.

`eta_seconds = (size_bytes - bytes_downloaded) / transfer_rate_bps` when rate > 0; null otherwise.

Stalled rows (substate `stalled`) suppress rate and ETA — they are misleading near zero.

## 6. API

### 6.1 New endpoint: `GET /api/queue`

Returns one row per visible `(asset, client)` pair. Sorted by:
1. `pipeline_status` priority: `transferring` < `queue` < `failed` < `ready`
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
- `status=queue,transferring,ready,failed` — comma-separated allowlist.
- `client_id=<id>` — restrict to one client.

Auth: same UI bearer token as `GET /api/assets`.

### 6.2 Modified endpoint: `GET /api/clients/{id}/assignments`

Currently returns `state` per assignment ("queued" | "ready" | etc., as established by bug #28 fix). This is replaced with `pipeline_status` (same enum as §4) plus `pipeline_substate`. The rest of the response shape is unchanged.

This is a breaking change to the field name. Acceptable because the only consumer is the LibraryScreen pill component in this same repo, updated in lockstep.

### 6.3 Unchanged

- `GET /api/assets` — keeps `Asset.status` semantics.
- All agent endpoints (`GET /assignments`, `POST /confirm`, `POST /download`, `/reconcile`).
- Auth tokens, secrets, deployment.

### 6.4 Schema additions

Two timestamp columns; one Alembic migration:

```sql
ALTER TABLE assignments ADD COLUMN bytes_downloaded_updated_at DATETIME NULL;
ALTER TABLE clients ADD COLUMN last_poll_at DATETIME NULL;
```

No backfill needed — null values map cleanly to "stalled?" and "offline?" semantics on first read (offline if null + assignment exists; not stalled until first byte update).

`bytes_downloaded_updated_at` is set whenever the agent reports a *changed* `bytes_downloaded`. Unchanged reports do not bump the timestamp (this is what makes stalled detectable).

`last_poll_at` is set in the FastAPI agent dependency that authenticates the bearer token (single write site).

## 7. UI

### 7.1 QueueScreen — `ui/src/screens/QueueScreen.tsx`

- Data source switches from `getAllAssets()` to `getQueue()` (new API client).
- Row identity becomes per-(asset, client). Each row carries a `→ {client_id}` chip, always visible (no special-case for single-client today; stable as the system grows).
- Badge driven by `row.pipeline_status`. Color mapping:
  - `queue` → existing "queued" treatment
  - `transferring` → existing "transcoding" amber treatment
  - `ready` → existing "ready" green
  - `failed` → existing "failed" red
- Secondary detail line under the filename, small muted text, rendered when `row.pipeline_detail` is non-null.
- Progress bar appears only when `pipeline_status === "transferring"`. Determinate when `0 < bytes < size`; indeterminate pulse when `substate === "verifying"`.
- A new metrics line beside `"X MB / Y MB"`: `"3.2 MB/s · ETA 2m 39s"` when both `transfer_rate_bps` and `eta_seconds` are non-null. Hidden when `substate === "stalled"`.
- Filter tabs: `All / Queue / Transferring / Ready / Failed`. Counts derived client-side from rendered rows.
- Show-name grouping (the existing `parseShowGroup` parser) is preserved.

### 7.2 LibraryScreen pills — `ui/src/screens/LibraryScreen.tsx` and components

The `BulkSyncPill` and per-episode pills consume the new `pipeline_status` field on `GET /api/clients/{id}/assignments`. Mapping:

| pipeline_status | pill |
|---|---|
| `queue` | grey "queued" |
| `transferring` | amber "syncing" (reuses transcoding token) |
| `ready` | green "ready" |
| `failed` | red "failed" |

Pills do not render the secondary detail line, progress bar, transfer rate, or ETA — they remain compact summary surfaces. Click-through to the Queue is a future enhancement; out of scope here.

### 7.3 Polling

Unchanged: 15s `refetchInterval` on QueueScreen; existing cadence on LibraryScreen.

## 8. Testing

### 8.1 Unit tests — `server/tests/test_pipeline.py` (new)

One test per row of the §5.2 truth table. Plus:

- Stalled threshold edges (1 second under, 1 second over).
- Agent-offline threshold edges.
- Transfer rate with 0/1/2/8 samples.
- ETA when rate is zero or null (returns null).
- ETA when bytes ≥ size — substate is `verifying`, which suppresses both rate and eta (both null).
- Substate `stalled` suppresses transfer_rate and eta.
- Row 7 (offline) takes precedence over row 8 (waiting).

### 8.2 Integration tests — `server/tests/test_routes_queue.py` (new)

Black-box against FastAPI test client + temp SQLite:

- Empty DB returns `{"rows": []}`.
- Asset+assignment fixtures for each pipeline_status value.
- Two assignments on one asset → two rows, distinct client_ids, independent statuses.
- Evicted/evicting assignments excluded.
- `?status=` filter (single + multi-value).
- `?client_id=` filter.
- Sort priority assertion.
- Auth: missing/wrong bearer → 401.
- Cross-projection regression: assert that `GET /api/clients/{id}/assignments` returns the same `pipeline_status` for the same fixture as `GET /api/queue` does — guarantees both surfaces share the projection.

### 8.3 UI tests — `ui/src/screens/QueueScreen.test.tsx` (new or extended)

- Renders one row per (asset, client) from a fixture.
- Badge color per `pipeline_status`.
- Progress bar present iff `transferring`; determinate when bytes < size; indeterminate when `verifying`.
- `pipeline_detail` rendered when non-null.
- Transfer rate + ETA rendered when both non-null; hidden when `stalled`.
- Filter tabs filter rows; counts in subtitle update.
- Multi-client fixture renders two rows with distinct chips.
- Pill component fixture-driven test for each `pipeline_status` → pill color.

### 8.4 Manual verification (post-implementation)

Walked against `docker-host01` + `caravan-pi`:

1. Subscribe fresh ~700 MB passthrough → `Queue · waiting for agent`.
2. Agent picks up → `Transferring` with progress bar + non-zero MB/s.
3. Kill aria2 ≥4 min mid-transfer → `Transferring · stalled — no progress in Xm`.
4. Resume aria2 → row leaves stalled within one poll.
5. Power off caravan-pi ≥3 min → `Queue · agent offline (last seen Xm ago)`.
6. Power on, complete transfer → `Ready` only after agent confirm.
7. Subscribe transcoded item → `Queue · transcoding on server` until ffmpeg done.
8. LibraryScreen pills track Queue states for the same items.

This list belongs in the implementation plan, not this spec.

## 9. Migration / rollout

- Single Alembic migration adds the two timestamp columns.
- Server deployable independently of UI — the new `/api/queue` endpoint can ship before the UI consumes it. The existing `GET /api/assets` continues to work.
- UI swaps QueueScreen and LibraryScreen pill data sources in lockstep with the server change to `/api/clients/{id}/assignments` (the field rename `state → pipeline_status` is breaking for that endpoint specifically).
- No agent change required; older agents continue to work because the agent ↔ server contract is untouched.

## 10. Open questions

- **Failed taxonomy growth.** Today only `transcode_failed` produces `pipeline_status = failed`. When download retry budgets land (post-MVP), a `download_failed` substate joins the table. The spec is forward-compatible but the threshold at which transient retries flip to terminal failure is undecided.
- **Library pill click-through to Queue.** Tempting UX win but not in scope.
- **Filter persistence.** Should the active filter tab survive page refresh? Existing screen does not persist; this spec preserves that.

## 11. Out of scope (explicit)

- Aria2 backpressure visibility (active vs waiting GIDs in aria2's internal queue) — deferred to Lab/syncarr backlog (2026-05-08 entry, priority: low).
- Real-time stale-Ready detection beyond `/reconcile`.
- Bug #34 (etag and absolute download URL) — separate ticket.
- Bug #37 (DELETE /assets wiping subscriptions) — separate ticket.
- Multi-client UX at scale (>3 clients) — taxonomy supports it; visual treatment of dense per-client expansion is a future design pass.

## 12. Acceptance criteria

The change is complete when:

1. `pytest server/tests/test_pipeline.py` covers every row of the §5.2 table, plus all §8.1 edges, and passes.
2. `pytest server/tests/test_routes_queue.py` covers the §8.2 list and passes.
3. `npm test` covers §8.3 list and passes.
4. The §8.4 manual checklist is walked end-to-end against caravan-pi and recorded in `Obsidian/Lab/syncarr/learnings-2026-05-08-pipeline-status.md`.
5. No row in the production Queue UI displays `Ready` while `bytes_downloaded < size_bytes` for that row's assignment.
6. No code path outside `pipeline.project()` constructs a `pipeline_status` value (enforced by code review; the projection function is the only writer).
