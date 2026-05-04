# Domain Model

## Entities

### Client

A satellite that subscribes to content. There can be many.

```
clients
  id                  TEXT PRIMARY KEY      -- uuid or short slug, e.g. "caravan"
  name                TEXT NOT NULL
  auth_token          TEXT NOT NULL         -- bearer token, rotatable
  storage_budget_bytes INTEGER              -- soft limit, used for UI warnings
  last_seen           TIMESTAMP             -- updated on each /assignments poll
  created_at          TIMESTAMP NOT NULL
```

### Profile

A transcoding configuration. Predefined by the user, referenced by subscriptions.

```
profiles
  id                  TEXT PRIMARY KEY      -- e.g. "5gb_1080p_h265"
  name                TEXT NOT NULL         -- human-readable
  ffmpeg_args         TEXT NOT NULL         -- JSON array of args
  target_size_bytes   INTEGER               -- used for size-budget UI hints
  created_at          TIMESTAMP NOT NULL
```

A "passthrough" profile (no transcode, copy as-is) is a valid profile.

### Subscription

A user's declared intent: "this client should have this content at this profile."

```
subscriptions
  id                  INTEGER PRIMARY KEY
  client_id           TEXT NOT NULL REFERENCES clients(id)
  media_item_id       TEXT NOT NULL         -- provider-specific item ID (e.g. Plex ratingKey)
  scope               TEXT NOT NULL         -- see scope formats below
  profile_id          TEXT NOT NULL REFERENCES profiles(id)
  created_at          TIMESTAMP NOT NULL
```

**Scope formats** (string-encoded for flexibility):

- `movie` — single movie (media_item_id is the movie)
- `show:all` — all episodes of a show
- `show:seasons:[2,3]` — specific seasons
- `show:latest:N` — latest N episodes (re-evaluated on each resolve)
- `show:season:S:from:E` — from S0xE0y onwards (e.g. follow ongoing season)

The MVP supports `movie` and `show:seasons:[N,M,...]` only. Other scopes are deferred.

### Asset

A transcoded file derived from a source media + profile. The atomic unit of storage and transfer.

```
assets
  id                  INTEGER PRIMARY KEY
  source_media_id     TEXT NOT NULL         -- provider-specific ID of the source media (e.g. Plex ratingKey)
  profile_id          TEXT NOT NULL REFERENCES profiles(id)
  source_path         TEXT NOT NULL         -- where the source lives on disk
  cache_path          TEXT                  -- where the transcoded file lives (NULL until ready)
  size_bytes          INTEGER               -- of transcoded output
  sha256              TEXT                  -- of transcoded output
  status              TEXT NOT NULL         -- 'queued' | 'transcoding' | 'ready' | 'failed'
  status_detail       TEXT                  -- error message if failed, progress if transcoding
  created_at          TIMESTAMP NOT NULL
  ready_at            TIMESTAMP

  UNIQUE(source_media_id, profile_id)
```

The `UNIQUE(source_media_id, profile_id)` constraint is the deduplication mechanism. If two subscriptions both want Bluey S02E01 at 5GB, they share one asset.

### Assignment

A many-to-many between clients and assets. Tracks delivery state per client per asset.

```
assignments
  client_id           TEXT NOT NULL REFERENCES clients(id)
  asset_id            INTEGER NOT NULL REFERENCES assets(id)
  state               TEXT NOT NULL         -- 'pending' | 'delivered' | 'evict'
  created_at          TIMESTAMP NOT NULL
  delivered_at        TIMESTAMP
  evict_requested_at  TIMESTAMP

  PRIMARY KEY (client_id, asset_id)
```

## State machines

### Asset lifecycle

```
[create] ─► queued ─► transcoding ─► ready
                          │
                          └────────► failed
                                       │
                                  (manual retry or
                                   subscription removal)

[no remaining assignments] ─► [delete row, remove cache_path file]
```

### Assignment lifecycle

```
[subscription created] ─► pending ─► delivered
                            │           │
                            │           ▼
                            │      [subscription removed]
                            │           │
                            └───────────┴───► evict ─► [agent confirms] ─► [delete row]
```

The `pending → evict` transition (without ever being delivered) happens when a subscription is removed before the agent finishes downloading. The agent should still confirm-evict after deleting any partial file.

## Subscription resolution

The "subscription resolver" is the function that turns subscriptions into assignments. It runs:

- Immediately on subscription create/update/delete
- Periodically (e.g. hourly) for subscriptions with dynamic scopes (`show:latest:N`)
- On Plex library change webhook (deferred, optional)

Pseudocode:

```python
def resolve_all_subscriptions():
    desired_assignments = set()

    for sub in db.subscriptions:
        items = provider.expand_scope(sub.media_item_id, sub.scope)
        for item in items:
            asset = get_or_create_asset(item.provider_id, sub.profile_id, item.file_path)
            desired_assignments.add((sub.client_id, asset.id))

    # Split current assignments by state
    active_assignments = set(db.query(
        "SELECT client_id, asset_id FROM assignments WHERE state IN ('pending', 'delivered')"
    ))
    evicting_assignments = set(db.query(
        "SELECT client_id, asset_id FROM assignments WHERE state = 'evict'"
    ))
    current_assignments = active_assignments | evicting_assignments

    # New assignments: INSERT if not present at all; flip evict → pending if re-subscribing
    for (client_id, asset_id) in desired_assignments:
        if (client_id, asset_id) in evicting_assignments:
            # Cancel the in-progress eviction
            db.update_assignment(client_id, asset_id, state='pending', evict_requested_at=None)
        elif (client_id, asset_id) not in active_assignments:
            db.insert_assignment(client_id, asset_id, state='pending')

    # Removed assignments → flip to evict
    for (client_id, asset_id) in current_assignments - desired_assignments:
        if (client_id, asset_id) in active_assignments:  # don't double-evict
            db.update_assignment(client_id, asset_id, state='evict')

    # GC orphaned assets (no assignments at all)
    db.execute("""
        DELETE FROM assets
        WHERE id NOT IN (SELECT asset_id FROM assignments)
    """)
    # Also delete their cache_path files (in a cleanup pass after commit)
```

Note: assets in `evict` state still count as assignments for the purpose of GC. They're only fully removed after agent confirms eviction.

## Effective state projection (assignments → agent API)

The agent API derives an effective `state` field for each assignment. Assignments in `delivered` state are omitted from the response entirely — the agent has already done its job.

| assignment.state | asset.status          | effective state returned to agent |
|------------------|-----------------------|-----------------------------------|
| evict            | any                   | evict                             |
| pending          | queued / transcoding  | queued                            |
| pending          | failed                | queued (agent waits; server retries or user intervenes) |
| pending          | ready                 | ready                             |
| delivered        | any                   | (omitted)                         |

## Edge cases worth knowing

**Profile changed on existing subscription**: this is a delete-and-recreate from the assignment perspective. Old (client_id, old_asset_id) flips to evict. New (client_id, new_asset_id) created as pending. Old asset evicted from cache once no client references it.

**Source file changed in Plex**: if Plex re-imports a file (different bitrate, edition, etc.), the `source_plex_id` may stay the same but the actual file is different. The transcoded asset is now stale. Detection mechanism: store source file mtime/size on the asset row, re-check on resolve, mark stale assets for re-transcode. **Deferred from MVP**; assume sources don't change.

**Same content in multiple libraries**: out of scope for MVP. Subscriptions reference provider-specific item IDs (e.g. Plex ratingKeys, Jellyfin itemIds), which are assumed globally unique within a provider instance.

**Client disappears permanently**: keep the row. UI should expose a "remove client" action that cascades to remove all subscriptions and assignments. Don't auto-detect.
