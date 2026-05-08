# Codex Review — Pipeline Status Redesign Spec

## TL;DR
Needs significant rework before implementation. The core goal is right: Queue and Library need a single server-side projection over cache state plus per-client delivery state. However, the spec currently disagrees with the actual persisted assignment state machine, the existing progress writer path, multi-profile identity, and its own rollout/API compatibility story. Fix those before writing code; otherwise the redesign will likely ship a cleaner UI over an ambiguous or partially unreachable model.

## BLOCKERS
1. Spec §5.2 uses `assignment.state = evicted`, but the persisted state machine uses `evict`, not `evicted`. In `server/src/syncarr_server/models.py`, `Assignment.state` is free text, and the actual states used by `server/src/syncarr_server/resolver.py` and `server/src/syncarr_server/routes/agent.py` are `pending`, `delivered`, and `evict`; `evicted` is only a confirm payload value. As written, a valid row with `assignment.state == "evict"` and a null/missing `evict_requested_at` is not covered by the truth table, despite §5.1 requiring `project()` to be total.

2. Spec §5.2 makes `asset.status = failed` win before `assignment.state = delivered`. That means a delivered assignment can project as `failed` if the asset row is later failed/reset by manual repair, data drift, or a retry path. Existing domain semantics in `docs/DOMAIN.md` say delivered assignments are already on the satellite and are omitted from the agent API regardless of asset status; spec §2 also says `Ready` corresponds to `Assignment.state = delivered`. If delivered is terminal for this projection, row 6 needs to precede transcode failure or the spec must explicitly define why a verified-on-disk file stops being `ready`.

3. Spec §5.3 names the wrong writer path for progress timestamps. It says the server updates `bytes_downloaded_updated_at` on a "confirm-style POST", but current progress is written by `PATCH /assignments/{asset_id}/progress` in `server/src/syncarr_server/routes/agent.py`. The implementation plan and tests must target `update_assignment_progress()`, including "only bump when bytes increase" and how to handle equal, lower, negative, or over-size reports.

4. Spec §5.1 and §5.5 are internally inconsistent: `project()` is declared pure with signature `(asset, assignment, client, now, poll_interval_seconds)`, but it returns `transfer_rate_bps` and `eta_seconds` that depend on a mutable in-memory sample buffer. Either the samples must be explicit input to the pure projection, or rate/ETA must be computed outside `project()` and merged later. As written, the "single source of truth" projection cannot produce all fields it promises.

5. Spec §6.2 proposes a breaking `state -> pipeline_status` rename on `GET /api/clients/{id}/assignments`, while §9 says "Server deployable independently of UI". Those conflict. The current UI in `ui/src/screens/LibraryScreen.tsx` and type in `ui/src/types.ts` require `state`; a server-only deploy would break Library pills. Make this additive (`state` retained, `pipeline_status` added) or remove the independent rollout claim and require a coordinated deploy.

6. Spec §6.2 keeps the rest of `GET /api/clients/{id}/assignments` unchanged, but the current response identity is only `media_item_id` plus `state` in `server/src/syncarr_server/schemas.py`. That cannot represent multi-profile assets for the same media item. The DB allows multiple assets per media item via `UNIQUE(source_media_id, profile_id)`, and the proposed `/api/queue` shape includes `profile_id` for this reason. The client-assignment response needs at least `asset_id` or `profile_id`, or the Library map will collapse two profile rows into one pill state.

7. Spec §5.4 proposes a new `clients.last_poll_at` column despite the existing `clients.last_seen` column, which `docs/DOMAIN.md`, `docs/API.md`, and `server/src/syncarr_server/routes/agent.py` already define/update on `GET /assignments`. If the semantics are truly "last authenticated agent request", that is not just a rename: updating it from the auth dependency would also write during `/download`, `/progress`, `/confirm`, and `/reconcile`, increasing SQLite write contention. The spec needs to choose: reuse/broaden `last_seen`, add a distinct column with a distinct meaning, or keep poll-only liveness.

8. Spec §5.2 has no explicit row for `assignment is None`, even though §5.1 allows `assignment: Assignment | None`. If `None` means invisible, say so in the truth table. If it means an unassigned asset row in `/api/queue`, that conflicts with §6.1's "one row per visible `(asset, client)` pair".

## IMPROVEMENTS
1. Client decommissioning is under-specified. `DELETE /clients/{id}` sets `Client.decommissioning = true`, deletes subscriptions, and flips assignments to `evict` until the agent confirms deletion. Spec §4 hides evicting rows from Queue and Library, so a decommissioning client with pending local deletes may disappear from the very surfaces operators use to understand work in flight. If hiding is intentional, the spec should state where decommission progress remains visible.

2. Subscription deletion mid-transfer is similarly hidden. `docs/ARCHITECTURE.md` says "Server evicts assignment while agent is downloading" recovers by having the agent cancel, delete partial data, and confirm. Spec §4 makes that active cancellation invisible because `evict_requested_at` rows vanish. Consider an `evicting` substate or an explicit "hidden from Queue but visible on Clients" rule.

3. `asset.status = ready` with a missing file is not addressed. `server/src/syncarr_server/routes/agent.py` returns 404 from `/download/{asset_id}` if the file path is missing, but the projection would still show `waiting_for_agent`, `transferring`, or `ready` based only on DB state. A pure projection should not do filesystem IO, but the spec needs an operator story for cache/source-file missing, especially for transcoded assets where `cache_path = NULL` is invalid but passthrough assets intentionally use `cache_path = NULL`.

4. Passthrough `sha256 = None` semantics need clearer wording. Current code in `server/src/syncarr_server/transcoder.py` intentionally leaves passthrough `sha256` null, and `server/src/syncarr_server/routes/agent.py` verifies size only. Spec §4/§5.2 labels `bytes >= size` as `verifying checksum`; that is misleading for passthrough and for any future non-hash verification path.

5. The stalled rule in §5.3 needs null and initial-progress semantics. §6.4 says null `bytes_downloaded_updated_at` maps cleanly to "not stalled until first byte update", but the formula `now - assignment.bytes_downloaded_updated_at` is undefined for null. State the exact behavior for `bytes_downloaded > 0` with null timestamp, which can happen after migrating a live DB.

6. The truth table should clamp or classify invalid byte combinations. Current models allow `bytes_downloaded` to be null, negative, equal to size, greater than size, or set while `asset.size_bytes` is null. Spec §5.2 only covers null/0, `0 < bytes < size`, and `bytes >= size`. The projection should define behavior for `size_bytes is None`, `bytes_downloaded < 0`, and `bytes_downloaded > size_bytes`.

7. Agent-offline precedence over active progress may hide the more important condition. Spec §5.2 row 7 only covers `bytes is null or 0` implicitly by order? As written, offline applies to any pending ready assignment before the bytes rows, so a half-downloaded transfer whose client stops polling becomes `queue/agent_offline`, not `transferring/stalled` or `transferring/offline`. That may be acceptable, but it should be deliberate and tested.

8. The `/api/queue` filter ergonomics in §6.1 should prefer repeated query params or support both repeated and comma-separated values. FastAPI handles repeated params naturally as `status=queue&status=ready`; comma-separated strings require custom parsing and clearer error behavior for unknown values.

9. Sort order in §6.1 puts `ready` rows last but does not define whether ready rows are retained indefinitely. If Queue is meant to be an operational work queue, `ready` rows may swamp active work over time. If it is a full delivery ledger, the endpoint should probably support `include_ready`, pagination, or a default status filter.

10. §6.4 says "single Alembic migration", but the repo currently uses SQLite WAL. Nullable `ALTER TABLE ... ADD COLUMN` is usually safe and fast, but it still requires an exclusive schema lock. The rollout section should say the server is quiesced or briefly restarted for migration, and tests should cover old DB startup plus migration.

11. Transfer-rate samples in §5.5 are not resilient across restart or multiple API workers. ADR-007 says MVP is a monolith, so this is probably acceptable, but the spec should state that rates are best-effort diagnostics and may be null after restart or when requests land in different processes.

12. `last_poll_at`/`last_seen` should not be updated by every authenticated `/download` request without thought. Range retries can turn file serving into DB writes, which cuts against `docs/DECISIONS.md` ADR-009's SQLite single-writer simplicity. Poll/progress/confirm writes are easier to reason about.

13. The spec does not cover checksum mismatch as an operator-relevant condition. `docs/API.md` says a confirm mismatch leaves the assignment pending and the agent will re-queue. Today that would likely show as `verifying`, then `waiting_for_agent` or `downloading` again, with no "last confirm failed" signal. That may be fine for MVP, but it is more actionable than raw rate/ETA in some failures.

14. Storage-full or agent-local aria2 failure is deferred only indirectly. `docs/ARCHITECTURE.md` says storage full leaves the server assignment pending and requires operator action. The new projection will likely render that as stalled or waiting; consider a future `agent_error` substate once the agent can report it.

15. The acceptance criterion in §12.6 says no code path outside `pipeline.project()` constructs a `pipeline_status`, but `/api/queue` filtering, sorting priority, OpenAPI/Pydantic literals, and UI type definitions necessarily mention the enum. Reword this to "no code path outside `pipeline.project()` decides/derives a row's pipeline_status".

## NITS
1. Spec §4 uses primary status `queue`, while the existing asset and UI vocabulary is `queued`. This is workable, but it creates needless mapping friction (`queue` status but "queued" badge/treatment). Consider `queued` unless there is a strong reason to use a noun.

2. Spec §5.2 says rows are evaluated top to bottom, but row 7's "client offline" precedence over rows 9-11 is only explained relative to row 8. Clarify that offline beats in-progress bytes too, if that is intended.

3. Spec §6.1 response uses `media_item_id`, while the model field is `source_media_id`. The public API already uses `media_item_id`, so this is fine; just keep naming consistent in tests.

4. Spec §8.4 says "This list belongs in the implementation plan, not this spec" inside the spec itself. Either remove that sentence from the design spec or move the checklist.

5. Spec §3 says real-time stale-Ready detection is non-goal because `/reconcile` covers agent disk drift. That does not cover server-side cache file drift for `/download`; those are different stale states.

6. Spec §6.1 auth says "same UI bearer token as `GET /api/assets`", but the route file currently declares `/assets` under the UI router. If `/api` is a mounted prefix elsewhere, no issue; otherwise keep examples consistent with the deployed path.

## QUESTIONS
1. Should delivered assignments always project as `ready` regardless of current `Asset.status`, or should asset failure invalidate the Library/Queue delivery status?

2. Should evicting assignments be invisible everywhere, or should operators have a visible "removing/canceling" state during subscription deletion and client decommissioning?

3. Is the product supposed to allow more than one active profile for the same `(client, media_item_id)`? The DB can represent it, but the current Library pill API cannot.

4. Should agent liveness mean "last assignment poll" or "last successful authenticated request of any kind"? Those answer different operator questions.

5. Is `/api/queue` intended to be an active-work screen or a historical/current-state ledger including all ready deliveries? The answer affects default filtering, pagination, and whether `ready` belongs in the default response.

6. Where should transfer-rate sample ownership live if `pipeline.project()` remains pure: route-local service, app state, or an explicit projection input?
