# Codex Review v2 — Pipeline Status Redesign Spec

## TL;DR
v2 is much closer and resolves all original blockers. It is nearly ready for planning, but I would not implement until the new v2 blockers below are fixed: rate tracking is keyed by a nonexistent assignment id, the server has no defined source for the agent poll interval used by offline/stall thresholds, and the Library bulk-pill behavior is underspecified for multi-assignment states. The remaining v1 improvements are mostly resolved or consciously deferred, with eviction/decommission visibility still only partially specified.

## PART A — v1 issue resolution

### Blockers

**B1 — Persisted eviction state is `evict`, not `evicted`**
- Status: RESOLVED
- Spec v2 reference: §4, §5.2
- Notes: The truth table now hides `assignment.state == "evict"` and `evict_requested_at IS NOT NULL`; tests also call out both cases.

**B2 — Delivered assignments must win over later asset failure**
- Status: RESOLVED
- Spec v2 reference: §2, §4, §5.2
- Notes: Row 3 now projects `assignment.state == "delivered"` to `ready` before row 4 handles `asset.status == "failed"`.

**B3 — Progress timestamp writer path was wrong**
- Status: RESOLVED
- Spec v2 reference: §5.3, §8.3
- Notes: The spec now targets `update_assignment_progress()` at `server/src/syncarr_server/routes/agent.py` and names `PATCH /assignments/{asset_id}/progress`.

**B4 — Pure projection could not depend on mutable rate buffer**
- Status: RESOLVED
- Spec v2 reference: §5.1, §5.5
- Notes: `rate_samples` are now explicit projection input, preserving `project()` purity.

**B5 — `GET /api/clients/{id}/assignments` rename broke independent rollout**
- Status: RESOLVED
- Spec v2 reference: §6.2, §9
- Notes: `state` is retained and the new `pipeline_*`, `asset_id`, and `profile_id` fields are additive.

**B6 — Client assignment response could not represent multi-profile identity**
- Status: RESOLVED
- Spec v2 reference: §5.6, §6.2, §7.2
- Notes: The response now includes `asset_id` and `profile_id`, and the Library pill identity is changed to `(media_item_id, profile_id)`.

**B7 — New `clients.last_poll_at` duplicated/conflicted with existing `last_seen`**
- Status: RESOLVED
- Spec v2 reference: §5.4, §6.4
- Notes: v2 reuses `clients.last_seen` and explicitly keeps poll-only writes, matching `docs/DOMAIN.md` and `server/src/syncarr_server/routes/agent.py`.

**B8 — `assignment is None` was allowed but not covered**
- Status: RESOLVED
- Spec v2 reference: §5.1, §5.2
- Notes: Row 1 now returns `visible=False` for `assignment is None`.

### Improvements

**I1 — Client decommissioning visibility was under-specified**
- Status: PARTIAL
- Spec v2 reference: §4
- Notes: v2 deliberately hides evicting rows from Queue/Library and says decommissioning remains visible on the Clients screen via `GET /api/clients`. It still does not specify whether Clients shows pending eviction counts/progress; current `ClientSchema` only exposes `decommissioning`, not remaining assignment count.

**I2 — Subscription deletion mid-transfer was hidden without an operator story**
- Status: PARTIAL
- Spec v2 reference: §4, §5.2
- Notes: Hiding is now explicit. The same gap remains: the spec says deletion-in-flight belongs on Clients, but does not define the UI/API shape for subscription-level evictions or partial-delete progress.

**I3 — Ready asset with missing server file was not addressed**
- Status: DEFERRED-WITH-RATIONALE
- Spec v2 reference: §3, §11
- Notes: v2 explicitly excludes server-side cache file drift from pure projection and calls for a future cache-integrity ticket. That is an acceptable scope decision for this redesign.

**I4 — Passthrough verification wording implied checksum verification**
- Status: RESOLVED
- Spec v2 reference: §4, §8.1
- Notes: `verifying` is now generic, and detail text differentiates `sha256 is None` passthrough assets as size verification.

**I5 — Stalled detection needed null timestamp semantics**
- Status: RESOLVED
- Spec v2 reference: §5.3, §6.4
- Notes: Null `bytes_downloaded_updated_at` now explicitly means not stalled.

**I6 — Invalid byte combinations needed defined projection behavior**
- Status: RESOLVED
- Spec v2 reference: §5.2, §8.1
- Notes: v2 defines behavior for unknown size, null/negative bytes, and over-size bytes, with corresponding test requirements.

**I7 — Agent-offline precedence over active progress needed to be deliberate**
- Status: RESOLVED
- Spec v2 reference: §5.2, §8.1
- Notes: Row 7 explicitly wins over rows 8-11, with rationale and required precedence tests.

**I8 — `/api/queue` filter parsing should use repeated query params**
- Status: RESOLVED
- Spec v2 reference: §6.1, §8.2
- Notes: v2 uses repeated `status=queued&status=transferring` and explicitly rejects comma-separated custom parsing.

**I9 — Ready-row retention needed active-work vs ledger decision**
- Status: RESOLVED
- Spec v2 reference: §6.1, §10
- Notes: v2 states `/api/queue` is a current-state ledger and includes `ready` by default, with pagination/default changes deferred until scale requires them.

**I10 — SQLite migration safety needed rollout notes**
- Status: RESOLVED
- Spec v2 reference: §6.4, §9
- Notes: v2 describes nullable `ALTER TABLE`, the existing restart/deploy flow, and entrypoint migration timing.

**I11 — Rate/ETA buffer resilience needed caveats**
- Status: RESOLVED
- Spec v2 reference: §5.5, §10
- Notes: v2 states the buffer is in-process, best-effort, empty after restart, and fragmented under multiple workers.

**I12 — `last_seen` should not be bumped by every authenticated agent request**
- Status: RESOLVED
- Spec v2 reference: §5.4
- Notes: v2 keeps `last_seen` as assignment-poll-only and explicitly avoids `/download`, `/progress`, and `/confirm` writes.

**I13 — Checksum mismatch lacks an operator-visible condition**
- Status: UNRESOLVED
- Spec v2 reference: §11
- Notes: v2 discusses future failed taxonomy growth but still does not mention confirm checksum/size mismatch. Current `server/src/syncarr_server/routes/agent.py` leaves the assignment pending after mismatch, so the UI may show a normal verifying/downloading loop with no last-failure signal.

**I14 — Storage-full or agent-local aria2 failure needs future taxonomy**
- Status: DEFERRED-WITH-RATIONALE
- Spec v2 reference: §3, §11
- Notes: v2 defers aria2/backpressure and future `download_failed` taxonomy because it requires an agent contract change. That is reasonable for this no-agent-change spec, though it should remain a backlog item.

**I15 — Acceptance criterion over-forbade enum mentions outside projection**
- Status: RESOLVED
- Spec v2 reference: §12
- Notes: §12.7 now says no code path outside `pipeline.project()` decides or derives status, while allowing endpoint definitions, types, and filters to mention the enum.

## PART B — New issues in v2

### NEW BLOCKERS
1. §5.5 keys `RateTracker` by `assignment_id: int`, but `server/src/syncarr_server/models.py` defines `Assignment` with composite primary key `(client_id, asset_id)` and no integer `id`. This will either not implement or will accidentally merge samples across clients for the same asset. Key the tracker by `(client_id, asset_id)` or another real assignment identity, and update §5.5/§8.1/§8.2 accordingly.

2. §5.1/§5.3/§5.4 require `poll_interval_seconds`, but the server has no agent poll interval setting today; `server/src/syncarr_server/config.py` only has `transcode_poll_interval_seconds`, and `ui/src/screens/QueueScreen.tsx`'s 15s refetch interval is unrelated to the satellite agent's `/assignments` cadence. The spec must define the source/default for this value before implementation, otherwise offline/stalled thresholds will be arbitrary.

3. §7.2 says both `BulkSyncPill` and per-episode pills consume `pipeline_status`, but `ui/src/screens/LibraryScreen.tsx`'s `BulkSyncPill` represents a show/season subscription intent that can expand to many assignments with mixed statuses. The spec needs either an aggregate bulk-pill rule or an explicit statement that bulk pills stay subscription-intent-only while per-episode/media-item pills use projection status.

### NEW IMPROVEMENTS
1. §5.3 says over-size progress compares `payload.bytes_downloaded > assignment.size_bytes`, but `Assignment` has no `size_bytes`; the value lives on `Asset` (`server/src/syncarr_server/models.py`). Reword to `asset.size_bytes`, and state behavior when `asset.size_bytes is None`.

2. §6.1 sorts by `created_at desc`, but each queue row has both assignment and asset creation times available. Specify `Assignment.created_at` if the intent is delivery work ordering; otherwise implementers may sort by `Asset.created_at` and produce surprising multi-client ordering.

3. §8.2 allows invalid `status` to return "400 or 422". Since FastAPI/Pydantic validation with a `Literal` query list naturally produces 422, pick one expected status code so tests and API behavior are deterministic.

4. §8.3 says concurrent identical progress posts are idempotent and the timestamp reflects the last bump. That should be reconciled with §5.3's equal-value rule: equal values do not bump, so only a strictly increasing write should advance `bytes_downloaded_updated_at`.

### NEW NITS
1. §5.3's writer-path line references `server/src/syncarr_server/routes/agent.py:225`; line numbers are already slightly unstable. Prefer the function name plus route path.

2. §6.1 uses a Unicode arrow in "`→ {client_id}` chip". The rest of the spec is mostly ASCII; use "to {client_id}" unless the UI is explicitly meant to render the arrow.

3. §8.5 says the manual verification list belongs in the implementation plan, but it is still present as a spec section. Either keep the acceptance criterion in §12.5 or remove §8.5 from the design spec.

### REMAINING QUESTIONS
1. For decommissioning and subscription deletion, should the Clients screen show only a boolean `decommissioning`, or should it show remaining evicting assignment count/progress? This affects whether §4's "remains visible on Clients screen" is operationally useful.

2. Should checksum/size mismatch get a persisted "last delivery error" field in a future agent/server contract, or is the intended MVP behavior simply to loop through retry/stalled states with logs only?

3. When multiple profiles exist for one `(client, media_item_id)`, what exact text should the Library render for each pill: client name only, profile name, or client + profile? §7.2 defines identity but not the visible label.
