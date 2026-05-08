# Pipeline Status Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the server-side `Asset.status`-driven Queue and Library pill rendering with a single pure projection over both the server cache axis (`Asset.status`) and the per-client delivery axis (`Assignment.state`, `bytes_downloaded`), surfacing stalled transfers, agent-offline conditions, transfer rate, and ETA.

**Architecture:** A new `pipeline.py` module exports one pure `project()` function that maps `(Asset, Assignment | None, Client | None, now, poll_interval, rate_samples)` to a `PipelineProjection` dataclass. Both the new `GET /api/queue` endpoint and the modified `GET /api/clients/{id}/assignments` endpoint call this function, guaranteeing identical status values across both surfaces. An in-process `RateTracker` accumulates `bytes_downloaded` samples keyed by `(client_id, asset_id)` for rate/ETA computation.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, SQLite WAL, Alembic, pytest-asyncio, httpx; TypeScript + React + TanStack Query (UI).

**Server tasks 1–8 can be deployed before UI tasks 9–11** — the API changes in Tasks 7–8 are strictly additive.

---

## File Map

### New files
| File | Responsibility |
|---|---|
| `server/src/syncarr_server/pipeline.py` | Pure projection function, types, `RateSample` |
| `server/src/syncarr_server/services/__init__.py` | Package init |
| `server/src/syncarr_server/services/rate_tracker.py` | In-process rate sample buffer, module-level singleton |
| `server/alembic/versions/0004_pipeline_status_columns.py` | Migration: 3 new nullable `assignments` columns |
| `server/tests/test_pipeline.py` | Unit tests: full truth table + all §8.1 edges |
| `server/tests/test_routes_queue.py` | Integration tests: `GET /api/queue` + cross-projection regression |

### Modified files
| File | Change |
|---|---|
| `server/src/syncarr_server/models.py` | Add 3 new nullable columns to `Assignment` |
| `server/src/syncarr_server/config.py` | Add `agent_poll_interval_seconds: int = 300` |
| `server/src/syncarr_server/schemas.py` | Add `QueueRowSchema`, `QueueResponse`; extend `ClientAssignmentSchema` |
| `server/src/syncarr_server/routes/agent.py` | Enhance `update_assignment_progress` + `confirm_asset`; add `ge=0` to `AgentProgressRequest` |
| `server/src/syncarr_server/routes/ui.py` | Add `GET /queue` endpoint; rewrite `list_client_assignments` to use `project()` |
| `server/tests/test_agent_routes.py` | Extend with §8.3 progress-writer and confirm-error tests |
| `ui/src/types.ts` | Extend `ClientAssignment`; add `QueueRow`, `PipelineStatus` |
| `ui/src/api.ts` | Add `getQueue()` |
| `ui/src/screens/QueueScreen.tsx` | Switch to `getQueue()`, pipeline-status-driven badges, rate/ETA line |
| `ui/src/screens/LibraryScreen.tsx` | Pills use `pipeline_status`; `BulkSyncPill` aggregates children |

---

## Task 1: DB Migration + Model Columns

**Files:**
- Create: `server/alembic/versions/0004_pipeline_status_columns.py`
- Modify: `server/src/syncarr_server/models.py`

- [ ] **Step 1: Add 3 nullable columns to `Assignment` model**

In `server/src/syncarr_server/models.py`, add these three fields to the `Assignment` class after `bytes_downloaded`:

```python
bytes_downloaded_updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
last_confirm_error_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
last_confirm_error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: Create Alembic migration**

Create `server/alembic/versions/0004_pipeline_status_columns.py`:

```python
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_pipeline_status_columns"
down_revision: str | None = "0003_assignment_progress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assignments") as batch_op:
        batch_op.add_column(sa.Column("bytes_downloaded_updated_at", sa.TIMESTAMP(), nullable=True))
        batch_op.add_column(sa.Column("last_confirm_error_at", sa.TIMESTAMP(), nullable=True))
        batch_op.add_column(sa.Column("last_confirm_error_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assignments") as batch_op:
        batch_op.drop_column("last_confirm_error_reason")
        batch_op.drop_column("last_confirm_error_at")
        batch_op.drop_column("bytes_downloaded_updated_at")
```

- [ ] **Step 3: Apply migration and verify**

```bash
cd server
DATABASE_URL=sqlite:////tmp/test_migration.db alembic upgrade head
sqlite3 /tmp/test_migration.db ".schema assignments"
```

Expected: `assignments` table has `bytes_downloaded_updated_at`, `last_confirm_error_at`, `last_confirm_error_reason` columns.

- [ ] **Step 4: Run existing tests to confirm no regression**

```bash
cd server
pytest tests/ -x -q
```

Expected: all existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add server/src/syncarr_server/models.py server/alembic/versions/0004_pipeline_status_columns.py
git commit -m "feat: add pipeline-status columns to assignments (migration 0004)"
```

---

## Task 2: Config Addition

**Files:**
- Modify: `server/src/syncarr_server/config.py`

- [ ] **Step 1: Add `agent_poll_interval_seconds` to `Settings`**

In `server/src/syncarr_server/config.py`, add after `transcode_poll_interval_seconds`:

```python
agent_poll_interval_seconds: int = 300
```

The full Settings class should now read:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(secrets_dir="/run/secrets")

    database_url: str = "sqlite+aiosqlite:////data/syncarr.db"
    media_cache_path: str = "/mnt/cache"
    transcode_poll_interval_seconds: int = 30
    agent_poll_interval_seconds: int = 300
    ui_token: str = ""
    media_provider_type: str = "plex"
    media_server_url: str = ""
    media_server_token: str = ""
    media_server_path_prefix: str = "/media"
    local_path_prefix: str = "/mnt/media"
```

- [ ] **Step 2: Verify**

```bash
cd server
python -c "from syncarr_server.config import get_settings; s = get_settings(); print(s.agent_poll_interval_seconds)"
```

Expected: `300`

- [ ] **Step 3: Commit**

```bash
git add server/src/syncarr_server/config.py
git commit -m "feat: add agent_poll_interval_seconds setting (default 300s)"
```

---

## Task 3: RateTracker Service

**Files:**
- Create: `server/src/syncarr_server/services/__init__.py`
- Create: `server/src/syncarr_server/services/rate_tracker.py`

The `RateTracker` accumulates `RateSample` objects (defined in `pipeline.py`) per `(client_id, asset_id)` key. It is a simple in-process buffer — best-effort diagnostics, not authoritative metrics. After server restart the buffer is empty; rate/ETA will be null until ≥2 samples arrive.

Note: `RateSample` is defined in `pipeline.py` (Task 4). Write the tracker first — it just imports the type.

- [ ] **Step 1: Create package init**

Create `server/src/syncarr_server/services/__init__.py` as an empty file.

- [ ] **Step 2: Write the failing test**

Create `server/tests/test_rate_tracker.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from syncarr_server.pipeline import RateSample
from syncarr_server.services.rate_tracker import RateTracker


def _sample(seconds_offset: int, bytes_downloaded: int) -> RateSample:
    return RateSample(
        at=datetime(2026, 1, 1, 0, 0, seconds_offset, tzinfo=UTC),
        bytes_downloaded=bytes_downloaded,
    )


def test_empty_returns_empty_sequence() -> None:
    tracker = RateTracker()
    assert list(tracker.samples_for(("client-1", 1))) == []


def test_record_and_retrieve() -> None:
    tracker = RateTracker()
    s = _sample(0, 1000)
    tracker.record(("client-1", 1), s)
    assert list(tracker.samples_for(("client-1", 1))) == [s]


def test_different_keys_are_isolated() -> None:
    tracker = RateTracker()
    tracker.record(("client-a", 1), _sample(0, 1000))
    tracker.record(("client-b", 1), _sample(0, 2000))
    assert tracker.samples_for(("client-a", 1))[0].bytes_downloaded == 1000
    assert tracker.samples_for(("client-b", 1))[0].bytes_downloaded == 2000


def test_max_samples_evicts_oldest() -> None:
    tracker = RateTracker(max_samples=3)
    key = ("c", 1)
    for i in range(5):
        tracker.record(key, _sample(i, i * 1000))
    samples = list(tracker.samples_for(key))
    assert len(samples) == 3
    assert samples[0].bytes_downloaded == 2000   # oldest kept
    assert samples[-1].bytes_downloaded == 4000  # newest


def test_module_singleton_exists() -> None:
    from syncarr_server.services.rate_tracker import rate_tracker
    assert isinstance(rate_tracker, RateTracker)
```

- [ ] **Step 3: Run to confirm failure**

```bash
cd server
pytest tests/test_rate_tracker.py -v
```

Expected: `ImportError` — module doesn't exist yet.

- [ ] **Step 4: Create `pipeline.py` stub (just the `RateSample` type needed by tracker)**

Create `server/src/syncarr_server/pipeline.py` with only the `RateSample` dataclass for now (the full projection function is Task 4):

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RateSample:
    at: datetime
    bytes_downloaded: int
```

- [ ] **Step 5: Create `rate_tracker.py`**

Create `server/src/syncarr_server/services/rate_tracker.py`:

```python
from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from syncarr_server.pipeline import RateSample

AssignmentKey = tuple[str, int]  # (client_id, asset_id)


class RateTracker:
    def __init__(self, max_samples: int = 8) -> None:
        self._samples: dict[AssignmentKey, deque[RateSample]] = {}
        self._max_samples = max_samples

    def record(self, key: AssignmentKey, sample: RateSample) -> None:
        if key not in self._samples:
            self._samples[key] = deque(maxlen=self._max_samples)
        self._samples[key].append(sample)

    def samples_for(self, key: AssignmentKey) -> Sequence[RateSample]:
        return list(self._samples.get(key, []))


rate_tracker = RateTracker()
```

- [ ] **Step 6: Run tests**

```bash
cd server
pytest tests/test_rate_tracker.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 7: Commit**

```bash
git add server/src/syncarr_server/pipeline.py \
        server/src/syncarr_server/services/__init__.py \
        server/src/syncarr_server/services/rate_tracker.py \
        server/tests/test_rate_tracker.py
git commit -m "feat: add RateTracker service and RateSample type stub"
```

---

## Task 4: `pipeline.py` — Full Projection Function

**Files:**
- Modify: `server/src/syncarr_server/pipeline.py` (replace stub with full implementation)
- Create: `server/tests/test_pipeline.py`

The projection function implements the 12-row decision table. It is pure: no DB, no network, no filesystem, no side effects. Time is an explicit parameter so tests can pin it.

- [ ] **Step 1: Write the full test suite**

Create `server/tests/test_pipeline.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from syncarr_server.models import Asset, Assignment, Client
from syncarr_server.pipeline import PipelineProjection, RateSample, project

# ── Helpers ──────────────────────────────────────────────────────────────────

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
POLL = 300  # default poll interval (seconds)


def make_asset(**kwargs: object) -> Asset:
    defaults: dict[str, object] = {
        "id": 1,
        "source_media_id": "media-1",
        "profile_id": "p1",
        "source_path": "/mnt/media/movie.mkv",
        "cache_path": None,
        "size_bytes": 1_000_000,
        "sha256": "abc123",
        "status": "ready",
        "status_detail": None,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "ready_at": None,
    }
    defaults.update(kwargs)
    a = Asset()
    for k, v in defaults.items():
        setattr(a, k, v)
    return a


def make_assignment(**kwargs: object) -> Assignment:
    defaults: dict[str, object] = {
        "client_id": "client-1",
        "asset_id": 1,
        "state": "pending",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "delivered_at": None,
        "evict_requested_at": None,
        "bytes_downloaded": None,
        "bytes_downloaded_updated_at": None,
        "last_confirm_error_at": None,
        "last_confirm_error_reason": None,
    }
    defaults.update(kwargs)
    a = Assignment()
    for k, v in defaults.items():
        setattr(a, k, v)
    return a


def make_client(**kwargs: object) -> Client:
    defaults: dict[str, object] = {
        "id": "client-1",
        "name": "Caravan Pi",
        "auth_token": "tok",
        "storage_budget_bytes": None,
        "last_seen": NOW - timedelta(seconds=POLL // 2),  # recently seen
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "decommissioning": False,
    }
    defaults.update(kwargs)
    c = Client()
    for k, v in defaults.items():
        setattr(c, k, v)
    return c


def proj(**kwargs: object) -> PipelineProjection:
    asset = kwargs.pop("asset", make_asset())  # type: ignore[arg-type]
    assignment = kwargs.pop("assignment", make_assignment())  # type: ignore[arg-type]
    client = kwargs.pop("client", make_client())  # type: ignore[arg-type]
    rate_samples = kwargs.pop("rate_samples", ())  # type: ignore[arg-type]
    confirm_error_recent_window_seconds = kwargs.pop("confirm_error_recent_window_seconds", 3600)  # type: ignore[arg-type]
    return project(
        asset,  # type: ignore[arg-type]
        assignment,  # type: ignore[arg-type]
        client,  # type: ignore[arg-type]
        now=NOW,
        poll_interval_seconds=POLL,
        rate_samples=rate_samples,  # type: ignore[arg-type]
        confirm_error_recent_window_seconds=confirm_error_recent_window_seconds,  # type: ignore[arg-type]
    )


# ── Truth table rows ──────────────────────────────────────────────────────────

def test_row1_no_assignment_invisible() -> None:
    result = proj(assignment=None)
    assert result.visible is False
    assert result.status is None


def test_row2_evict_state_invisible() -> None:
    result = proj(assignment=make_assignment(state="evict"))
    assert result.visible is False


def test_row2_evict_requested_at_invisible() -> None:
    result = proj(assignment=make_assignment(evict_requested_at=NOW))
    assert result.visible is False


def test_row2_both_evict_conditions_invisible() -> None:
    result = proj(assignment=make_assignment(state="evict", evict_requested_at=NOW))
    assert result.visible is False


def test_row3_delivered_is_ready() -> None:
    result = proj(assignment=make_assignment(state="delivered"))
    assert result.visible is True
    assert result.status == "ready"
    assert result.substate == "delivered"
    assert result.detail is None


def test_row3_delivered_wins_over_asset_failed() -> None:
    # Row 3 before Row 4 — file is on satellite, server failure irrelevant
    result = proj(
        asset=make_asset(status="failed"),
        assignment=make_assignment(state="delivered"),
    )
    assert result.status == "ready"
    assert result.substate == "delivered"


def test_row4_asset_failed() -> None:
    result = proj(
        asset=make_asset(status="failed", status_detail="ffmpeg exited with code 1"),
        assignment=make_assignment(state="pending"),
    )
    assert result.status == "failed"
    assert result.substate == "transcode_failed"
    assert result.detail == "transcode failed: ffmpeg exited with code 1"


def test_row4_asset_failed_no_detail() -> None:
    result = proj(
        asset=make_asset(status="failed", status_detail=None),
        assignment=make_assignment(state="pending"),
    )
    assert result.detail == "transcode failed: unknown error"


def test_row5_asset_queued() -> None:
    result = proj(asset=make_asset(status="queued"))
    assert result.status == "queued"
    assert result.substate == "transcoding_pending"
    assert result.detail == "waiting to transcode"


def test_row6_asset_transcoding() -> None:
    result = proj(asset=make_asset(status="transcoding"))
    assert result.status == "queued"
    assert result.substate == "transcoding"
    assert result.detail == "transcoding on server"


def test_row7_client_offline_wins_over_rows_8_to_11() -> None:
    # last_seen > 3*POLL ago → offline
    offline_client = make_client(last_seen=NOW - timedelta(seconds=3 * POLL + 1))
    # bytes_downloaded > 0 (would be row 12 if online)
    result = proj(
        client=offline_client,
        assignment=make_assignment(bytes_downloaded=500_000),
    )
    assert result.status == "queued"
    assert result.substate == "agent_offline"
    assert "offline" in (result.detail or "")


def test_row7_client_offline_last_seen_none() -> None:
    offline_client = make_client(last_seen=None)
    result = proj(client=offline_client)
    assert result.substate == "agent_offline"


def test_row7_offline_threshold_exactly_at_boundary() -> None:
    # max(3 * 300, 180) = 900s
    # exactly at threshold: NOT offline
    barely_online_client = make_client(last_seen=NOW - timedelta(seconds=900))
    result = proj(client=barely_online_client)
    assert result.substate != "agent_offline"


def test_row7_offline_one_second_over_threshold() -> None:
    offline_client = make_client(last_seen=NOW - timedelta(seconds=901))
    result = proj(client=offline_client)
    assert result.substate == "agent_offline"


def test_row8_size_bytes_none_waiting_for_agent() -> None:
    result = proj(asset=make_asset(size_bytes=None))
    assert result.status == "queued"
    assert result.substate == "waiting_for_agent"


def test_row9_bytes_downloaded_none_waiting_for_agent() -> None:
    result = proj(assignment=make_assignment(bytes_downloaded=None))
    assert result.substate == "waiting_for_agent"


def test_row9_bytes_downloaded_zero_waiting_for_agent() -> None:
    result = proj(assignment=make_assignment(bytes_downloaded=0))
    assert result.substate == "waiting_for_agent"


def test_row10_bytes_gte_size_verifying_with_sha256() -> None:
    result = proj(
        asset=make_asset(size_bytes=1_000_000, sha256="abc"),
        assignment=make_assignment(bytes_downloaded=1_000_000),
    )
    assert result.status == "transferring"
    assert result.substate == "verifying"
    assert result.detail == "verifying checksum"
    assert result.transfer_rate_bps is None  # suppressed
    assert result.eta_seconds is None         # suppressed


def test_row10_bytes_gte_size_verifying_passthrough() -> None:
    result = proj(
        asset=make_asset(size_bytes=1_000_000, sha256=None),
        assignment=make_assignment(bytes_downloaded=1_000_000),
    )
    assert result.substate == "verifying"
    assert result.detail == "verifying size"


def test_row10_bytes_over_size_treated_as_verifying() -> None:
    result = proj(
        asset=make_asset(size_bytes=1_000_000),
        assignment=make_assignment(bytes_downloaded=1_100_000),
    )
    assert result.substate == "verifying"


def test_row11_stalled() -> None:
    # threshold = max(2 * 300, 120) = 600s
    stale_ts = NOW - timedelta(seconds=601)
    result = proj(
        assignment=make_assignment(
            bytes_downloaded=500_000,
            bytes_downloaded_updated_at=stale_ts,
        )
    )
    assert result.status == "transferring"
    assert result.substate == "stalled"
    assert "stalled" in (result.detail or "")


def test_row11_stalled_threshold_boundary_not_stalled() -> None:
    # exactly at threshold (600s): NOT stalled
    ts = NOW - timedelta(seconds=600)
    result = proj(
        assignment=make_assignment(bytes_downloaded=500_000, bytes_downloaded_updated_at=ts)
    )
    assert result.substate != "stalled"


def test_row11_stalled_one_second_over_threshold() -> None:
    ts = NOW - timedelta(seconds=601)
    result = proj(
        assignment=make_assignment(bytes_downloaded=500_000, bytes_downloaded_updated_at=ts)
    )
    assert result.substate == "stalled"


def test_row11_stalled_null_updated_at_not_stalled() -> None:
    # bytes_downloaded_updated_at IS NULL → not stalled
    result = proj(
        assignment=make_assignment(bytes_downloaded=500_000, bytes_downloaded_updated_at=None)
    )
    assert result.substate != "stalled"


def test_row12_downloading() -> None:
    result = proj(assignment=make_assignment(bytes_downloaded=250_000))
    assert result.status == "transferring"
    assert result.substate == "downloading"
    assert result.detail is None


def test_row12_downloading_with_rate() -> None:
    samples = [
        RateSample(at=NOW - timedelta(seconds=2), bytes_downloaded=100_000),
        RateSample(at=NOW, bytes_downloaded=300_000),
    ]
    result = proj(
        asset=make_asset(size_bytes=1_000_000),
        assignment=make_assignment(bytes_downloaded=300_000),
        rate_samples=samples,
    )
    assert result.transfer_rate_bps == pytest.approx(100_000.0)
    assert result.eta_seconds == pytest.approx(7.0)


def test_row12_rate_zero_samples_is_none() -> None:
    result = proj(assignment=make_assignment(bytes_downloaded=250_000), rate_samples=())
    assert result.transfer_rate_bps is None
    assert result.eta_seconds is None


def test_row12_rate_one_sample_is_none() -> None:
    samples = [RateSample(at=NOW, bytes_downloaded=250_000)]
    result = proj(assignment=make_assignment(bytes_downloaded=250_000), rate_samples=samples)
    assert result.transfer_rate_bps is None


def test_row12_rate_two_samples_under_1s_is_none() -> None:
    samples = [
        RateSample(at=NOW - timedelta(milliseconds=500), bytes_downloaded=200_000),
        RateSample(at=NOW, bytes_downloaded=300_000),
    ]
    result = proj(assignment=make_assignment(bytes_downloaded=300_000), rate_samples=samples)
    assert result.transfer_rate_bps is None


def test_stalled_suppresses_rate_and_eta() -> None:
    ts = NOW - timedelta(seconds=601)
    samples = [
        RateSample(at=NOW - timedelta(seconds=2), bytes_downloaded=100_000),
        RateSample(at=NOW, bytes_downloaded=300_000),
    ]
    result = proj(
        assignment=make_assignment(bytes_downloaded=300_000, bytes_downloaded_updated_at=ts),
        rate_samples=samples,
    )
    assert result.substate == "stalled"
    assert result.transfer_rate_bps is None
    assert result.eta_seconds is None


def test_verifying_suppresses_rate_and_eta() -> None:
    samples = [
        RateSample(at=NOW - timedelta(seconds=2), bytes_downloaded=900_000),
        RateSample(at=NOW, bytes_downloaded=1_000_000),
    ]
    result = proj(
        asset=make_asset(size_bytes=1_000_000),
        assignment=make_assignment(bytes_downloaded=1_000_000),
        rate_samples=samples,
    )
    assert result.substate == "verifying"
    assert result.transfer_rate_bps is None


# ── Invalid byte combinations ─────────────────────────────────────────────────

def test_bytes_negative_treated_as_zero() -> None:
    result = proj(assignment=make_assignment(bytes_downloaded=-1))
    assert result.substate == "waiting_for_agent"


# ── §5.7 Confirm-error detail enhancement ────────────────────────────────────

def test_confirm_error_downloading_within_window() -> None:
    error_at = NOW - timedelta(seconds=100)
    result = proj(
        assignment=make_assignment(
            bytes_downloaded=300_000,
            last_confirm_error_at=error_at,
            last_confirm_error_reason="checksum_mismatch",
        )
    )
    assert result.substate == "downloading"
    assert result.detail == "last attempt failed: checksum_mismatch — retrying"


def test_confirm_error_verifying_within_window() -> None:
    error_at = NOW - timedelta(seconds=100)
    result = proj(
        asset=make_asset(size_bytes=1_000_000),
        assignment=make_assignment(
            bytes_downloaded=1_000_000,
            last_confirm_error_at=error_at,
            last_confirm_error_reason="size_mismatch",
        )
    )
    assert result.substate == "verifying"
    assert result.detail == "verifying after recent size_mismatch"


def test_confirm_error_stalled_not_overridden() -> None:
    error_at = NOW - timedelta(seconds=100)
    stale_ts = NOW - timedelta(seconds=601)
    result = proj(
        assignment=make_assignment(
            bytes_downloaded=300_000,
            bytes_downloaded_updated_at=stale_ts,
            last_confirm_error_at=error_at,
            last_confirm_error_reason="checksum_mismatch",
        )
    )
    assert result.substate == "stalled"
    assert "checksum_mismatch" not in (result.detail or "")


def test_confirm_error_outside_window_ignored() -> None:
    error_at = NOW - timedelta(seconds=3700)  # > 1h window
    result = proj(
        assignment=make_assignment(
            bytes_downloaded=300_000,
            last_confirm_error_at=error_at,
            last_confirm_error_reason="checksum_mismatch",
        )
    )
    assert result.substate == "downloading"
    assert result.detail is None


# ── bytes_downloaded on projection output ────────────────────────────────────

def test_bytes_downloaded_propagated_to_projection() -> None:
    result = proj(assignment=make_assignment(bytes_downloaded=400_000))
    assert result.bytes_downloaded == 400_000


def test_size_bytes_propagated_to_projection() -> None:
    result = proj(asset=make_asset(size_bytes=2_000_000))
    assert result.size_bytes == 2_000_000
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd server
pytest tests/test_pipeline.py -v 2>&1 | head -20
```

Expected: `ImportError` or `AttributeError` — types/function not yet defined.

- [ ] **Step 3: Implement the full `pipeline.py`**

Replace the stub in `server/src/syncarr_server/pipeline.py` with:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from syncarr_server.models import Asset, Assignment, Client

PipelineStatus = Literal["queued", "transferring", "ready", "failed"]
PipelineSubstate = Literal[
    "transcoding_pending",
    "transcoding",
    "waiting_for_agent",
    "agent_offline",
    "downloading",
    "verifying",
    "stalled",
    "delivered",
    "transcode_failed",
]


@dataclass(frozen=True)
class RateSample:
    at: datetime
    bytes_downloaded: int


@dataclass(frozen=True)
class PipelineProjection:
    visible: bool
    status: PipelineStatus | None = None
    substate: PipelineSubstate | None = None
    detail: str | None = None
    bytes_downloaded: int | None = None
    size_bytes: int | None = None
    transfer_rate_bps: float | None = None
    eta_seconds: float | None = None


def _utc(dt: datetime) -> datetime:
    """Ensure datetime is UTC-aware."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _is_client_offline(client: Client | None, now: datetime, poll_interval_seconds: int) -> bool:
    if client is None:
        return False
    if client.last_seen is None:
        return True
    threshold = timedelta(seconds=max(3 * poll_interval_seconds, 180))
    return (now - _utc(client.last_seen)) > threshold


def _is_stalled(assignment: Assignment, now: datetime, poll_interval_seconds: int) -> bool:
    if assignment.bytes_downloaded_updated_at is None:
        return False
    threshold = timedelta(seconds=max(2 * poll_interval_seconds, 120))
    return (now - _utc(assignment.bytes_downloaded_updated_at)) > threshold


def _compute_rate(samples: Sequence[RateSample]) -> float | None:
    if len(samples) < 2:
        return None
    span = (samples[-1].at - samples[0].at).total_seconds()
    if span <= 1.0:
        return None
    return (samples[-1].bytes_downloaded - samples[0].bytes_downloaded) / span


def _confirm_error_detail(
    assignment: Assignment,
    substate: PipelineSubstate,
    now: datetime,
    window_seconds: int,
) -> str | None:
    if assignment.last_confirm_error_at is None:
        return None
    if substate in ("stalled", "agent_offline"):
        return None
    if (now - _utc(assignment.last_confirm_error_at)) > timedelta(seconds=window_seconds):
        return None
    reason = assignment.last_confirm_error_reason or "unknown"
    if substate == "downloading":
        return f"last attempt failed: {reason} — retrying"
    if substate == "verifying":
        return f"verifying after recent {reason}"
    return None


def project(
    asset: Asset,
    assignment: Assignment | None,
    client: Client | None,
    *,
    now: datetime,
    poll_interval_seconds: int,
    rate_samples: Sequence[RateSample] = (),
    confirm_error_recent_window_seconds: int = 3600,
) -> PipelineProjection:
    # Row 1: no assignment
    if assignment is None:
        return PipelineProjection(visible=False)

    # Row 2: evicting
    if assignment.state == "evict" or assignment.evict_requested_at is not None:
        return PipelineProjection(visible=False)

    # Row 3: delivered — terminal, wins over asset failure
    if assignment.state == "delivered":
        return PipelineProjection(
            visible=True,
            status="ready",
            substate="delivered",
            bytes_downloaded=assignment.bytes_downloaded,
            size_bytes=asset.size_bytes,
        )

    # Row 4: asset failed
    if asset.status == "failed":
        return PipelineProjection(
            visible=True,
            status="failed",
            substate="transcode_failed",
            detail=f"transcode failed: {asset.status_detail or 'unknown error'}",
            size_bytes=asset.size_bytes,
        )

    # Row 5: queued (waiting to transcode)
    if asset.status == "queued":
        return PipelineProjection(
            visible=True,
            status="queued",
            substate="transcoding_pending",
            detail="waiting to transcode",
            size_bytes=asset.size_bytes,
        )

    # Row 6: transcoding
    if asset.status == "transcoding":
        return PipelineProjection(
            visible=True,
            status="queued",
            substate="transcoding",
            detail="transcoding on server",
            size_bytes=asset.size_bytes,
        )

    # asset.status == "ready" from here — pending assignment awaiting delivery

    # Row 7: client offline — wins over rows 8–11
    if _is_client_offline(client, now, poll_interval_seconds):
        if client is not None and client.last_seen is not None:
            elapsed_mins = int((now - _utc(client.last_seen)).total_seconds() / 60)
            detail = f"agent offline (last seen {elapsed_mins}m ago)"
        else:
            detail = "agent offline (never seen)"
        return PipelineProjection(
            visible=True,
            status="queued",
            substate="agent_offline",
            detail=detail,
            bytes_downloaded=assignment.bytes_downloaded,
            size_bytes=asset.size_bytes,
        )

    # Clamp bytes for rows 8–12
    raw_bytes = assignment.bytes_downloaded
    clamped = max(0, raw_bytes) if raw_bytes is not None else None

    # Row 8: size unknown
    if asset.size_bytes is None:
        return PipelineProjection(
            visible=True,
            status="queued",
            substate="waiting_for_agent",
            detail="waiting for agent to pick up",
            bytes_downloaded=clamped,
        )

    # Row 9: no meaningful progress
    if clamped is None or clamped <= 0:
        return PipelineProjection(
            visible=True,
            status="queued",
            substate="waiting_for_agent",
            detail="waiting for agent to pick up",
            bytes_downloaded=clamped,
            size_bytes=asset.size_bytes,
        )

    # Row 10: bytes >= size → verifying
    if clamped >= asset.size_bytes:
        base_detail = "verifying size" if asset.sha256 is None else "verifying checksum"
        error_detail = _confirm_error_detail(
            assignment, "verifying", now, confirm_error_recent_window_seconds
        )
        return PipelineProjection(
            visible=True,
            status="transferring",
            substate="verifying",
            detail=error_detail or base_detail,
            bytes_downloaded=clamped,
            size_bytes=asset.size_bytes,
        )

    # Row 11: stalled
    if _is_stalled(assignment, now, poll_interval_seconds):
        threshold_secs = max(2 * poll_interval_seconds, 120)
        return PipelineProjection(
            visible=True,
            status="transferring",
            substate="stalled",
            detail=f"stalled — no progress in {threshold_secs // 60}m",
            bytes_downloaded=clamped,
            size_bytes=asset.size_bytes,
        )

    # Row 12: downloading
    rate_bps = _compute_rate(rate_samples)
    eta: float | None = None
    if rate_bps is not None and rate_bps > 0:
        eta = (asset.size_bytes - clamped) / rate_bps

    download_detail = _confirm_error_detail(
        assignment, "downloading", now, confirm_error_recent_window_seconds
    )

    return PipelineProjection(
        visible=True,
        status="transferring",
        substate="downloading",
        detail=download_detail,
        bytes_downloaded=clamped,
        size_bytes=asset.size_bytes,
        transfer_rate_bps=rate_bps,
        eta_seconds=eta,
    )
```

- [ ] **Step 4: Run all pipeline tests**

```bash
cd server
pytest tests/test_pipeline.py tests/test_rate_tracker.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full server test suite**

```bash
cd server
pytest tests/ -x -q
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add server/src/syncarr_server/pipeline.py server/tests/test_pipeline.py
git commit -m "feat: pipeline.py projection function with full truth-table unit tests"
```

---

## Task 5: `update_assignment_progress` Enhancement

**Files:**
- Modify: `server/src/syncarr_server/schemas.py` — add `ge=0` to `AgentProgressRequest.bytes_downloaded`
- Modify: `server/src/syncarr_server/routes/agent.py` — stalled tracking + rate sample
- Modify: `server/tests/test_agent_routes.py` — extend with §8.3 progress-writer tests

The writer rules (from spec §5.3):
- Strictly increasing `bytes_downloaded` → update value AND advance `bytes_downloaded_updated_at`, record `RateSample`
- Equal value (heartbeat) → no change to value, **no** timestamp advance
- Decreasing value → no change, log warning
- Negative → 422 (Pydantic constraint)

- [ ] **Step 1: Write failing tests**

Append to `server/tests/test_agent_routes.py` (add after existing tests):

```python
# ── Progress writer §8.3 tests ────────────────────────────────────────────────

async def _create_ready_asset_and_assignment(
    session: AsyncSession,
    client_id: str = "c1",
    asset_id: int = 1,
) -> tuple[Asset, Assignment]:
    from syncarr_server.models import Asset, Assignment
    from datetime import UTC, datetime
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    asset = Asset()
    asset.id = asset_id
    asset.source_media_id = "m1"
    asset.profile_id = "p1"
    asset.source_path = "/mnt/media/movie.mkv"
    asset.size_bytes = 1_000_000
    asset.sha256 = "abc"
    asset.status = "ready"
    asset.status_detail = None
    asset.created_at = now
    asset.ready_at = now
    asset.cache_path = None
    session.add(asset)

    assignment = Assignment()
    assignment.client_id = client_id
    assignment.asset_id = asset_id
    assignment.state = "pending"
    assignment.created_at = now
    assignment.delivered_at = None
    assignment.evict_requested_at = None
    assignment.bytes_downloaded = None
    assignment.bytes_downloaded_updated_at = None
    assignment.last_confirm_error_at = None
    assignment.last_confirm_error_reason = None
    session.add(assignment)
    await session.commit()
    return asset, assignment


@pytest.mark.asyncio
async def test_progress_strictly_increasing_updates_value_and_timestamp(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_agent: dict[str, str],
) -> None:
    await _create_ready_asset_and_assignment(session)
    resp = await http_client.patch(
        "/assignments/1/progress",
        headers=auth_headers_agent,
        json={"bytes_downloaded": 500_000},
    )
    assert resp.status_code == 204
    from sqlalchemy import select
    from syncarr_server.models import Assignment
    result = await session.execute(
        select(Assignment).where(Assignment.client_id == "c1", Assignment.asset_id == 1)
    )
    a = result.scalar_one()
    assert a.bytes_downloaded == 500_000
    assert a.bytes_downloaded_updated_at is not None


@pytest.mark.asyncio
async def test_progress_equal_value_no_timestamp_advance(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_agent: dict[str, str],
) -> None:
    from datetime import UTC, datetime
    from sqlalchemy import update
    from syncarr_server.models import Assignment
    await _create_ready_asset_and_assignment(session)
    fixed_ts = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    await session.execute(
        update(Assignment)
        .where(Assignment.client_id == "c1", Assignment.asset_id == 1)
        .values(bytes_downloaded=300_000, bytes_downloaded_updated_at=fixed_ts)
    )
    await session.commit()

    resp = await http_client.patch(
        "/assignments/1/progress",
        headers=auth_headers_agent,
        json={"bytes_downloaded": 300_000},
    )
    assert resp.status_code == 204
    await session.refresh(
        (await session.execute(
            select(Assignment).where(Assignment.client_id == "c1")
        )).scalar_one()
    )
    result = await session.execute(
        select(Assignment).where(Assignment.client_id == "c1", Assignment.asset_id == 1)
    )
    a = result.scalar_one()
    assert a.bytes_downloaded == 300_000
    assert a.bytes_downloaded_updated_at == fixed_ts  # unchanged


@pytest.mark.asyncio
async def test_progress_decreasing_value_not_stored(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_agent: dict[str, str],
) -> None:
    from sqlalchemy import update
    from syncarr_server.models import Assignment
    await _create_ready_asset_and_assignment(session)
    await session.execute(
        update(Assignment)
        .where(Assignment.client_id == "c1", Assignment.asset_id == 1)
        .values(bytes_downloaded=500_000)
    )
    await session.commit()

    resp = await http_client.patch(
        "/assignments/1/progress",
        headers=auth_headers_agent,
        json={"bytes_downloaded": 200_000},
    )
    assert resp.status_code == 204
    result = await session.execute(
        select(Assignment).where(Assignment.client_id == "c1", Assignment.asset_id == 1)
    )
    a = result.scalar_one()
    assert a.bytes_downloaded == 500_000  # not lowered


@pytest.mark.asyncio
async def test_progress_negative_returns_422(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_agent: dict[str, str],
) -> None:
    await _create_ready_asset_and_assignment(session)
    resp = await http_client.patch(
        "/assignments/1/progress",
        headers=auth_headers_agent,
        json={"bytes_downloaded": -1},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd server
pytest tests/test_agent_routes.py -k "test_progress_" -v
```

Expected: failures (timestamp not updated, negative not rejected, etc.).

- [ ] **Step 3: Add `ge=0` constraint to `AgentProgressRequest`**

In `server/src/syncarr_server/schemas.py`, change:

```python
class AgentProgressRequest(Schema):
    bytes_downloaded: int
```

to:

```python
from pydantic import Field

class AgentProgressRequest(Schema):
    bytes_downloaded: int = Field(..., ge=0)
```

(Add `Field` to the existing pydantic imports at the top of `schemas.py`.)

- [ ] **Step 4: Rewrite `update_assignment_progress` in `agent.py`**

Add this import near the top of `server/src/syncarr_server/routes/agent.py`:

```python
from datetime import UTC, datetime

import structlog

from syncarr_server.pipeline import RateSample
from syncarr_server.services.rate_tracker import rate_tracker
```

Replace the existing `update_assignment_progress` function body:

```python
@router.patch(
    "/assignments/{asset_id}/progress",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_assignment_progress(
    asset_id: int,
    payload: AgentProgressRequest,
    client: Annotated[Client, Depends(require_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    logger = structlog.get_logger()
    assignment_asset = await _get_assignment_asset(session, client.id, asset_id)
    if assignment_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    assignment, _asset = assignment_asset
    if assignment.state not in ("pending",):
        return

    new_bytes = payload.bytes_downloaded
    current = assignment.bytes_downloaded or 0
    now = datetime.now(UTC)

    if new_bytes > current:
        assignment.bytes_downloaded = new_bytes
        assignment.bytes_downloaded_updated_at = now
        rate_tracker.record(
            (client.id, asset_id),
            RateSample(at=now, bytes_downloaded=new_bytes),
        )
    elif new_bytes == current:
        pass  # heartbeat — no timestamp advance
    else:
        logger.warning(
            "bytes_downloaded_decreased",
            client_id=client.id,
            asset_id=asset_id,
            current=current,
            received=new_bytes,
        )

    await session.commit()
```

- [ ] **Step 5: Run progress tests**

```bash
cd server
pytest tests/test_agent_routes.py -k "test_progress_" -v
```

Expected: all 4 progress tests pass.

- [ ] **Step 6: Run full test suite**

```bash
cd server
pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add server/src/syncarr_server/schemas.py \
        server/src/syncarr_server/routes/agent.py \
        server/tests/test_agent_routes.py
git commit -m "feat: progress writer tracks bytes_downloaded_updated_at and rate samples"
```

---

## Task 6: `confirm_asset` Enhancement

**Files:**
- Modify: `server/src/syncarr_server/routes/agent.py` — set/clear `last_confirm_error_*`
- Modify: `server/tests/test_agent_routes.py` — extend with §8.3 confirm-error tests

When the confirm returns `ok=False` (checksum or size mismatch), record the error on the assignment. When the confirm returns `ok=True` (delivered), clear the columns.

Also fix the existing passthrough mismatch path: currently it returns `reason="checksum_mismatch"` for size mismatches — change to `reason="size_mismatch"`.

- [ ] **Step 1: Write failing tests**

Append to `server/tests/test_agent_routes.py`:

```python
# ── Confirm-error §8.3 tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_confirm_checksum_mismatch_records_error(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_agent: dict[str, str],
) -> None:
    from syncarr_server.models import Asset, Assignment
    await _create_ready_asset_and_assignment(session)
    # Asset has sha256 "abc" — send wrong sha
    resp = await http_client.post(
        "/confirm/1",
        headers=auth_headers_agent,
        json={"state": "delivered", "actual_sha256": "wrong", "actual_size_bytes": 1_000_000},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False

    result = await session.execute(
        select(Assignment).where(Assignment.client_id == "c1", Assignment.asset_id == 1)
    )
    a = result.scalar_one()
    assert a.last_confirm_error_at is not None
    assert a.last_confirm_error_reason == "checksum_mismatch"
    assert a.state == "pending"  # not delivered


@pytest.mark.asyncio
async def test_confirm_size_mismatch_passthrough_records_size_mismatch(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_agent: dict[str, str],
) -> None:
    from sqlalchemy import update
    from syncarr_server.models import Asset, Assignment
    # Make asset passthrough (sha256=None)
    await _create_ready_asset_and_assignment(session)
    await session.execute(
        update(Asset).where(Asset.id == 1).values(sha256=None)
    )
    await session.commit()

    resp = await http_client.post(
        "/confirm/1",
        headers=auth_headers_agent,
        json={"state": "delivered", "actual_sha256": None, "actual_size_bytes": 999_999},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False

    result = await session.execute(
        select(Assignment).where(Assignment.client_id == "c1", Assignment.asset_id == 1)
    )
    a = result.scalar_one()
    assert a.last_confirm_error_reason == "size_mismatch"


@pytest.mark.asyncio
async def test_confirm_success_clears_error_columns(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_agent: dict[str, str],
) -> None:
    from datetime import UTC, datetime
    from sqlalchemy import update
    from syncarr_server.models import Assignment
    await _create_ready_asset_and_assignment(session)
    # Pre-populate error columns
    await session.execute(
        update(Assignment)
        .where(Assignment.client_id == "c1", Assignment.asset_id == 1)
        .values(
            last_confirm_error_at=datetime(2026, 1, 1, tzinfo=UTC),
            last_confirm_error_reason="checksum_mismatch",
        )
    )
    await session.commit()

    resp = await http_client.post(
        "/confirm/1",
        headers=auth_headers_agent,
        json={"state": "delivered", "actual_sha256": "abc", "actual_size_bytes": 1_000_000},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    result = await session.execute(
        select(Assignment).where(Assignment.client_id == "c1", Assignment.asset_id == 1)
    )
    a = result.scalar_one()
    assert a.state == "delivered"
    assert a.last_confirm_error_at is None
    assert a.last_confirm_error_reason is None
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd server
pytest tests/test_agent_routes.py -k "test_confirm_" -v
```

Expected: the 3 new tests fail.

- [ ] **Step 3: Update `confirm_asset` in `agent.py`**

Replace the delivered-confirm section of `confirm_asset` (after the `assert asset.status != "ready"` guard, replace the mismatch + success paths):

```python
    now = datetime.now(UTC)

    # For passthrough assets sha256=None; skip hash check but still verify size.
    if asset.sha256 is not None:
        if payload.actual_sha256 != asset.sha256 or payload.actual_size_bytes != asset.size_bytes:
            assignment.last_confirm_error_at = now
            assignment.last_confirm_error_reason = "checksum_mismatch"
            await session.commit()
            return AgentConfirmResponse(
                ok=False,
                reason="checksum_mismatch",
                expected_sha256=asset.sha256,
                actual_sha256=payload.actual_sha256,
            )
    elif asset.size_bytes is not None and payload.actual_size_bytes != asset.size_bytes:
        assignment.last_confirm_error_at = now
        assignment.last_confirm_error_reason = "size_mismatch"
        await session.commit()
        return AgentConfirmResponse(
            ok=False,
            reason="size_mismatch",
            expected_sha256=None,
            actual_sha256=payload.actual_sha256,
        )

    assignment.state = "delivered"
    assignment.delivered_at = now
    assignment.last_confirm_error_at = None
    assignment.last_confirm_error_reason = None
    await session.commit()
    return AgentConfirmResponse(ok=True)
```

Note: the `_utc_now()` call at the bottom of the original function must also be replaced — use `now` instead.

- [ ] **Step 4: Run confirm tests**

```bash
cd server
pytest tests/test_agent_routes.py -k "test_confirm_" -v
```

Expected: all 3 new tests pass.

- [ ] **Step 5: Run full test suite**

```bash
cd server
pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add server/src/syncarr_server/routes/agent.py server/tests/test_agent_routes.py
git commit -m "feat: confirm_asset records last_confirm_error columns on mismatch, clears on success"
```

---

## Task 7: `GET /api/queue` Endpoint

**Files:**
- Modify: `server/src/syncarr_server/schemas.py` — add `QueueRowSchema`, `QueueResponse`
- Modify: `server/src/syncarr_server/routes/ui.py` — add `GET /queue` route
- Create: `server/tests/test_routes_queue.py` — §8.2 integration tests

Sort order: `transferring` (0) < `queued` (1) < `failed` (2) < `ready` (3), then `Assignment.created_at` descending within each group.

- [ ] **Step 1: Add schemas**

In `server/src/syncarr_server/schemas.py`, add at the end (after existing classes). First add imports at the top of the file:

```python
from datetime import datetime
```

(if not already present — check the existing imports)

Then add the new schemas:

```python
class QueueRowSchema(Schema):
    asset_id: int
    client_id: str
    media_item_id: str
    filename: str
    profile_id: str
    size_bytes: int | None
    bytes_downloaded: int | None
    transfer_rate_bps: float | None
    eta_seconds: float | None
    pipeline_status: str
    pipeline_substate: str | None
    pipeline_detail: str | None
    delivered_at: datetime | None
    created_at: datetime


class QueueResponse(Schema):
    rows: list[QueueRowSchema]
```

- [ ] **Step 2: Write failing integration tests**

Create `server/tests/test_routes_queue.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.models import Asset, Assignment, Client, Profile

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


async def _seed_profile(session: AsyncSession, profile_id: str = "p1") -> None:
    p = Profile()
    p.id = profile_id
    p.name = profile_id
    p.ffmpeg_args = ["-c:v", "libx265"]
    p.target_size_bytes = None
    p.created_at = _NOW
    session.add(p)
    await session.commit()


async def _seed_client(session: AsyncSession, client_id: str = "c1", last_seen: datetime | None = None) -> None:
    c = Client()
    c.id = client_id
    c.name = client_id.title()
    c.auth_token = f"tok-{client_id}"
    c.storage_budget_bytes = None
    c.last_seen = last_seen or (_NOW - __import__('datetime').timedelta(seconds=60))
    c.created_at = _NOW
    c.decommissioning = False
    session.add(c)
    await session.commit()


async def _seed_asset(
    session: AsyncSession,
    asset_id: int = 1,
    status: str = "ready",
    size_bytes: int | None = 1_000_000,
    sha256: str | None = "abc",
    profile_id: str = "p1",
    source_media_id: str = "m1",
) -> None:
    a = Asset()
    a.id = asset_id
    a.source_media_id = source_media_id
    a.profile_id = profile_id
    a.source_path = f"/mnt/media/movie{asset_id}.mkv"
    a.cache_path = f"/mnt/cache/{asset_id}.mkv"
    a.size_bytes = size_bytes
    a.sha256 = sha256
    a.status = status
    a.status_detail = None
    a.created_at = _NOW
    a.ready_at = _NOW if status == "ready" else None
    session.add(a)
    await session.commit()


async def _seed_assignment(
    session: AsyncSession,
    client_id: str = "c1",
    asset_id: int = 1,
    state: str = "pending",
    bytes_downloaded: int | None = None,
    delivered_at: datetime | None = None,
) -> None:
    a = Assignment()
    a.client_id = client_id
    a.asset_id = asset_id
    a.state = state
    a.created_at = _NOW
    a.delivered_at = delivered_at
    a.evict_requested_at = None
    a.bytes_downloaded = bytes_downloaded
    a.bytes_downloaded_updated_at = None
    a.last_confirm_error_at = None
    a.last_confirm_error_reason = None
    session.add(a)
    await session.commit()


async def test_empty_db_returns_empty_rows(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    resp = await http_client.get("/queue", headers=auth_headers_ui)
    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


async def test_queued_asset_returns_row(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session, status="ready")
    await _seed_assignment(session, bytes_downloaded=None)

    resp = await http_client.get("/queue", headers=auth_headers_ui)
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["pipeline_status"] == "queued"
    assert rows[0]["client_id"] == "c1"
    assert rows[0]["asset_id"] == 1


async def test_delivered_assignment_returns_ready(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session)
    await _seed_assignment(session, state="delivered", delivered_at=_NOW)

    resp = await http_client.get("/queue", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    assert rows[0]["pipeline_status"] == "ready"


async def test_evicted_assignment_excluded(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session)
    await _seed_assignment(session, state="evict")

    resp = await http_client.get("/queue", headers=auth_headers_ui)
    assert resp.json()["rows"] == []


async def test_two_clients_same_asset_two_rows(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session, "c1")
    await _seed_client(session, "c2")
    await _seed_asset(session)
    await _seed_assignment(session, client_id="c1")
    await _seed_assignment(session, client_id="c2")

    resp = await http_client.get("/queue", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    assert len(rows) == 2
    client_ids = {r["client_id"] for r in rows}
    assert client_ids == {"c1", "c2"}


async def test_two_profiles_same_client_same_item_two_rows(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session, "p1")
    await _seed_profile(session, "p2")
    await _seed_client(session)
    await _seed_asset(session, asset_id=1, profile_id="p1", source_media_id="m1")
    await _seed_asset(session, asset_id=2, profile_id="p2", source_media_id="m1")
    await _seed_assignment(session, asset_id=1)
    await _seed_assignment(session, asset_id=2)

    resp = await http_client.get("/queue", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    assert len(rows) == 2
    profile_ids = {r["profile_id"] for r in rows}
    assert profile_ids == {"p1", "p2"}


async def test_status_filter_single_value(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session, asset_id=1)
    await _seed_asset(session, asset_id=2)
    await _seed_assignment(session, asset_id=1, state="delivered", delivered_at=_NOW)
    await _seed_assignment(session, asset_id=2)

    resp = await http_client.get("/queue?status=queued", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    assert all(r["pipeline_status"] == "queued" for r in rows)
    assert len(rows) == 1


async def test_status_filter_repeated_values(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session, asset_id=1)
    await _seed_asset(session, asset_id=2)
    await _seed_assignment(session, asset_id=1, state="delivered", delivered_at=_NOW)
    await _seed_assignment(session, asset_id=2)

    resp = await http_client.get("/queue?status=queued&status=ready", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    statuses = {r["pipeline_status"] for r in rows}
    assert statuses == {"queued", "ready"}


async def test_status_filter_invalid_returns_422(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    resp = await http_client.get("/queue?status=invalid", headers=auth_headers_ui)
    assert resp.status_code == 422


async def test_client_id_filter(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session, "c1")
    await _seed_client(session, "c2")
    await _seed_asset(session)
    await _seed_assignment(session, client_id="c1")
    await _seed_assignment(session, client_id="c2")

    resp = await http_client.get("/queue?client_id=c1", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    assert all(r["client_id"] == "c1" for r in rows)


async def test_missing_bearer_returns_401(
    http_client: AsyncClient,
) -> None:
    resp = await http_client.get("/queue")
    assert resp.status_code == 401


async def test_sort_transferring_before_queued_before_ready(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session, asset_id=1)
    await _seed_asset(session, asset_id=2)
    await _seed_asset(session, asset_id=3)
    await _seed_assignment(session, asset_id=1, state="delivered", delivered_at=_NOW)  # ready
    await _seed_assignment(session, asset_id=2)  # queued (no bytes)
    await _seed_assignment(session, asset_id=3, bytes_downloaded=100_000)  # transferring

    resp = await http_client.get("/queue", headers=auth_headers_ui)
    rows = resp.json()["rows"]
    statuses = [r["pipeline_status"] for r in rows]
    # transferring before queued before ready
    assert statuses.index("transferring") < statuses.index("queued")
    assert statuses.index("queued") < statuses.index("ready")
```

- [ ] **Step 3: Run to confirm failures**

```bash
cd server
pytest tests/test_routes_queue.py -v 2>&1 | head -30
```

Expected: 404 on `/queue` (endpoint doesn't exist yet).

- [ ] **Step 4: Add `GET /queue` route to `ui.py`**

Add these imports at the top of `server/src/syncarr_server/routes/ui.py` (if not present):

```python
from typing import Literal

from syncarr_server.config import get_settings
from syncarr_server.pipeline import project
from syncarr_server.services.rate_tracker import rate_tracker
from syncarr_server.schemas import QueueResponse, QueueRowSchema
```

Add the `_PIPELINE_SORT_ORDER` constant and the new route to `ui.py`:

```python
_PIPELINE_SORT_ORDER: dict[str, int] = {
    "transferring": 0,
    "queued": 1,
    "failed": 2,
    "ready": 3,
}


@router.get(
    "/queue",
    response_model=QueueResponse,
    dependencies=[Depends(require_ui_auth)],
)
async def get_queue(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[
        list[Literal["queued", "transferring", "ready", "failed"]] | None,
        Query(alias="status"),
    ] = None,
    client_id: str | None = None,
) -> QueueResponse:
    settings = get_settings()
    now = datetime.now(UTC)

    query = (
        select(Assignment, Asset, Client)
        .join(Asset, Assignment.asset_id == Asset.id)
        .join(Client, Assignment.client_id == Client.id)
        .order_by(Assignment.created_at.desc())
    )
    if client_id is not None:
        query = query.where(Assignment.client_id == client_id)

    result = await session.execute(query)

    rows: list[QueueRowSchema] = []
    for assignment, asset, client in result.all():
        samples = rate_tracker.samples_for((client.id, asset.id))
        p = project(
            asset,
            assignment,
            client,
            now=now,
            poll_interval_seconds=settings.agent_poll_interval_seconds,
            rate_samples=samples,
        )
        if not p.visible:
            continue
        if status is not None and p.status not in status:
            continue
        rows.append(
            QueueRowSchema(
                asset_id=asset.id,
                client_id=client.id,
                media_item_id=asset.source_media_id,
                filename=Path(asset.source_path).name,
                profile_id=asset.profile_id,
                size_bytes=p.size_bytes,
                bytes_downloaded=p.bytes_downloaded,
                transfer_rate_bps=p.transfer_rate_bps,
                eta_seconds=p.eta_seconds,
                pipeline_status=p.status,
                pipeline_substate=p.substate,
                pipeline_detail=p.detail,
                delivered_at=assignment.delivered_at,
                created_at=assignment.created_at,
            )
        )

    rows.sort(key=lambda r: (_PIPELINE_SORT_ORDER.get(r.pipeline_status, 99),))
    return QueueResponse(rows=rows)
```

Also add `Query` to the FastAPI imports at the top of `ui.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
```

And add `Path` to stdlib imports if not present:

```python
from pathlib import Path
```

- [ ] **Step 5: Run queue tests**

```bash
cd server
pytest tests/test_routes_queue.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run full test suite**

```bash
cd server
pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add server/src/syncarr_server/schemas.py \
        server/src/syncarr_server/routes/ui.py \
        server/tests/test_routes_queue.py
git commit -m "feat: add GET /api/queue endpoint with pipeline projection"
```

---

## Task 8: Extend `GET /api/clients/{id}/assignments`

**Files:**
- Modify: `server/src/syncarr_server/schemas.py` — extend `ClientAssignmentSchema`
- Modify: `server/src/syncarr_server/routes/ui.py` — rewrite `list_client_assignments`
- Modify: `server/tests/test_routes_queue.py` — add cross-projection regression + `list_client_assignments` tests

The change is **additive**: `state` is kept for backward compat. New fields are always populated.

- [ ] **Step 1: Write failing tests**

Append to `server/tests/test_routes_queue.py`:

```python
# ── list_client_assignments additive fields + cross-projection ──────────────

async def test_client_assignments_new_fields_populated(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session)
    await _seed_assignment(session)

    resp = await http_client.get("/clients/c1/assignments", headers=auth_headers_ui)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    # New fields
    assert "asset_id" in row
    assert "profile_id" in row
    assert "pipeline_status" in row
    assert "pipeline_substate" in row
    assert "pipeline_detail" in row
    # Old field still present
    assert "state" in row


async def test_client_assignments_multi_profile_two_rows(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    await _seed_profile(session, "p1")
    await _seed_profile(session, "p2")
    await _seed_client(session)
    await _seed_asset(session, asset_id=1, profile_id="p1", source_media_id="m1")
    await _seed_asset(session, asset_id=2, profile_id="p2", source_media_id="m1")
    await _seed_assignment(session, asset_id=1)
    await _seed_assignment(session, asset_id=2)

    resp = await http_client.get("/clients/c1/assignments", headers=auth_headers_ui)
    rows = resp.json()
    assert len(rows) == 2
    profile_ids = {r["profile_id"] for r in rows}
    assert profile_ids == {"p1", "p2"}


async def test_cross_projection_queue_matches_client_assignments(
    http_client: AsyncClient,
    session: AsyncSession,
    auth_headers_ui: dict[str, str],
) -> None:
    """Same fixture → same pipeline_status from both endpoints."""
    await _seed_profile(session)
    await _seed_client(session)
    await _seed_asset(session)
    await _seed_assignment(session, bytes_downloaded=300_000)

    queue_resp = await http_client.get("/queue", headers=auth_headers_ui)
    assign_resp = await http_client.get("/clients/c1/assignments", headers=auth_headers_ui)

    queue_status = queue_resp.json()["rows"][0]["pipeline_status"]
    assign_status = assign_resp.json()[0]["pipeline_status"]
    assert queue_status == assign_status
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd server
pytest tests/test_routes_queue.py -k "client_assignments" -v
```

Expected: failures — `asset_id`, `profile_id`, `pipeline_*` fields not present.

- [ ] **Step 3: Extend `ClientAssignmentSchema` in `schemas.py`**

Replace the existing `ClientAssignmentSchema`:

```python
class ClientAssignmentSchema(Schema):
    media_item_id: str
    state: AgentAssignmentState          # KEPT — backwards compat
    asset_id: int                         # NEW
    profile_id: str                       # NEW
    pipeline_status: str                  # NEW
    pipeline_substate: str | None         # NEW
    pipeline_detail: str | None           # NEW
```

- [ ] **Step 4: Rewrite `list_client_assignments` in `ui.py`**

Replace the current function body of `list_client_assignments`:

```python
@router.get(
    "/clients/{client_id}/assignments",
    response_model=list[ClientAssignmentSchema],
    dependencies=[Depends(require_ui_auth)],
)
async def list_client_assignments(
    client_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    media_item_ids: str = "",
) -> list[ClientAssignmentSchema]:
    client = await _get_client(session, client_id)
    settings = get_settings()
    now = datetime.now(UTC)

    ids = [id_.strip() for id_ in media_item_ids.split(",") if id_.strip()]

    query = (
        select(Assignment, Asset)
        .join(Asset, Assignment.asset_id == Asset.id)
        .where(Assignment.client_id == client_id)
        .order_by(Assignment.created_at, Assignment.asset_id)
    )
    if ids:
        query = query.where(Asset.source_media_id.in_(ids))

    result = await session.execute(query)

    assignments: list[ClientAssignmentSchema] = []
    for assignment, asset in result.all():
        samples = rate_tracker.samples_for((client.id, asset.id))
        p = project(
            asset,
            assignment,
            client,
            now=now,
            poll_interval_seconds=settings.agent_poll_interval_seconds,
            rate_samples=samples,
        )
        if not p.visible:
            continue
        assignments.append(
            ClientAssignmentSchema(
                media_item_id=asset.source_media_id,
                state=cast("AgentAssignmentState", p.status or "queued"),
                asset_id=asset.id,
                profile_id=asset.profile_id,
                pipeline_status=p.status or "queued",
                pipeline_substate=p.substate,
                pipeline_detail=p.detail,
            )
        )
    return assignments
```

Also remove the now-unused `_effective_state` import from `ui.py`:

```python
# Remove this line:
from syncarr_server.routes.agent import _effective_state
```

(`_effective_state` remains in `agent.py` for agent-facing routes — do not remove it there.)

- [ ] **Step 5: Run new tests**

```bash
cd server
pytest tests/test_routes_queue.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run full test suite**

```bash
cd server
pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 7: Type-check**

```bash
cd server
mypy src/ --strict
```

Expected: no new errors.

- [ ] **Step 8: Commit**

```bash
git add server/src/syncarr_server/schemas.py \
        server/src/syncarr_server/routes/ui.py \
        server/tests/test_routes_queue.py
git commit -m "feat: GET /clients/{id}/assignments now returns pipeline_status fields"
```

---

## Task 9: UI Types + API Client

**Files:**
- Modify: `ui/src/types.ts` — extend `ClientAssignment`, add `QueueRow`, `PipelineStatus`
- Modify: `ui/src/api.ts` — add `getQueue()`

No new dependencies needed.

- [ ] **Step 1: Add types to `ui/src/types.ts`**

Replace the existing `ClientAssignment` type and add `QueueRow` and `PipelineStatus`:

```typescript
export type PipelineStatus = 'queued' | 'transferring' | 'ready' | 'failed'

export type PipelineSubstate =
  | 'transcoding_pending'
  | 'transcoding'
  | 'waiting_for_agent'
  | 'agent_offline'
  | 'downloading'
  | 'verifying'
  | 'stalled'
  | 'delivered'
  | 'transcode_failed'

export type ClientAssignment = {
  media_item_id: string
  state: 'ready' | 'queued' | 'evict'   // KEPT — used by existing pill state checks
  asset_id: number                        // NEW
  profile_id: string                      // NEW
  pipeline_status: PipelineStatus         // NEW
  pipeline_substate: PipelineSubstate | null  // NEW
  pipeline_detail: string | null          // NEW
}

export type QueueRow = {
  asset_id: number
  client_id: string
  media_item_id: string
  filename: string
  profile_id: string
  size_bytes: number | null
  bytes_downloaded: number | null
  transfer_rate_bps: number | null
  eta_seconds: number | null
  pipeline_status: PipelineStatus
  pipeline_substate: PipelineSubstate | null
  pipeline_detail: string | null
  delivered_at: string | null
  created_at: string
}
```

- [ ] **Step 2: Add `getQueue()` to `ui/src/api.ts`**

Append to `api.ts`:

```typescript
export async function getQueue(params?: {
  status?: PipelineStatus[]
  client_id?: string
}): Promise<{ rows: QueueRow[] }> {
  const search = new URLSearchParams()
  if (params?.status) {
    for (const s of params.status) search.append('status', s)
  }
  if (params?.client_id) search.set('client_id', params.client_id)
  const qs = search.toString()
  return apiFetch<{ rows: QueueRow[] }>(`/queue${qs ? `?${qs}` : ''}`)
}
```

Also add the import at the top of `api.ts`:

```typescript
import type { ..., QueueRow, PipelineStatus } from './types'
```

(Add `QueueRow` and `PipelineStatus` to the existing type import from `./types`.)

- [ ] **Step 3: TypeScript check**

```bash
cd ui
npm run typecheck
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add ui/src/types.ts ui/src/api.ts
git commit -m "feat: add QueueRow type and getQueue() API client function"
```

---

## Task 10: QueueScreen Redesign

**Files:**
- Modify: `ui/src/screens/QueueScreen.tsx`

Switch from `getAllAssets()` + `Asset.status` to `getQueue()` + `pipeline_status`. Key changes:
- Row identity: `(asset_id, client_id)` instead of `asset_id` alone
- Badge driven by `pipeline_status`
- Detail line when `pipeline_detail` non-null
- Rate + ETA metrics line
- Progress bar: indeterminate when `verifying`, hide rate/ETA on `stalled`/`verifying`
- Filter tabs: `All / Queued / Transferring / Ready / Failed` (values match `PipelineStatus`)
- Remove the `deleteAsset` mutation (server owns eviction; `DELETE /assets` is bug #37 — removed per spec §3)

- [ ] **Step 1: Rewrite `QueueScreen.tsx`**

Replace the full content of `ui/src/screens/QueueScreen.tsx`:

```typescript
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getQueue } from '../api'
import { Badge } from '../components/Badge'
import { PillTabs } from '../components/PillTabs'
import type { PipelineStatus, QueueRow } from '../types'

type BadgeColor = 'ready' | 'transcoding' | 'queued' | 'failed' | 'default'

function pipelineStatusToBadgeColor(status: PipelineStatus): BadgeColor {
  if (status === 'ready') return 'ready'
  if (status === 'transferring') return 'transcoding'
  if (status === 'queued') return 'queued'
  if (status === 'failed') return 'failed'
  return 'default'
}

const PIPELINE_SORT_ORDER: Record<PipelineStatus, number> = {
  transferring: 0,
  queued: 1,
  failed: 2,
  ready: 3,
}

function formatBytes(bytes: number | null): string {
  if (bytes === null) return '–'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function formatRate(bps: number): string {
  if (bps < 1024) return `${bps.toFixed(0)} B/s`
  if (bps < 1024 ** 2) return `${(bps / 1024).toFixed(1)} KB/s`
  return `${(bps / 1024 ** 2).toFixed(1)} MB/s`
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  if (mins < 60) return `${mins}m ${secs}s`
  const hours = Math.floor(mins / 60)
  return `${hours}h ${mins % 60}m`
}

function parseShowGroup(filename: string): string {
  const tvMatch = filename.match(/^(.+?)\s+-\s+S\d{2}E\d{2}/i)
  if (tvMatch) return tvMatch[1]
  const dashIdx = filename.indexOf(' - ')
  return dashIdx > 0 ? filename.slice(0, dashIdx) : filename
}

function QueueRowItem({ row }: { row: QueueRow }) {
  const isTransferring = row.pipeline_status === 'transferring'
  const isVerifying = row.pipeline_substate === 'verifying'
  const isStalled = row.pipeline_substate === 'stalled'

  const hasDeterminateProgress =
    isTransferring &&
    !isVerifying &&
    row.bytes_downloaded != null &&
    row.size_bytes != null &&
    row.size_bytes > 0 &&
    row.bytes_downloaded < row.size_bytes

  const showRateEta =
    isTransferring &&
    !isVerifying &&
    !isStalled &&
    row.transfer_rate_bps != null &&
    row.eta_seconds != null

  return (
    <div className="queue-row">
      <div className="queue-row__status">
        <Badge
          color={pipelineStatusToBadgeColor(row.pipeline_status)}
          label={row.pipeline_status === 'transferring' ? 'syncing' : row.pipeline_status}
        />
        <span className="queue-row__client" style={{ fontSize: '0.75rem', color: 'var(--color-text-muted, #888)' }}>
          to {row.client_id}
        </span>
      </div>
      <div className="queue-row__main">
        <span className="queue-row__filename">{row.filename}</span>
        {isTransferring ? (
          <div className="queue-row__progress">
            <div className="progress__track" aria-hidden="true">
              {isVerifying ? (
                <div className="progress__fill progress__fill--indeterminate" />
              ) : hasDeterminateProgress ? (
                <div
                  className="progress__fill"
                  style={{
                    width: `${Math.min(100, (row.bytes_downloaded! / row.size_bytes!) * 100).toFixed(1)}%`,
                  }}
                />
              ) : (
                <div className="progress__fill progress__fill--indeterminate" />
              )}
            </div>
            {hasDeterminateProgress ? (
              <span className="progress__label" style={{ fontSize: '0.75rem', color: 'var(--color-text-muted, #888)', marginTop: '2px' }}>
                {formatBytes(row.bytes_downloaded)} / {formatBytes(row.size_bytes)}
                {showRateEta ? (
                  <span style={{ marginLeft: '0.5rem' }}>
                    · {formatRate(row.transfer_rate_bps!)} · ETA {formatEta(row.eta_seconds!)}
                  </span>
                ) : null}
              </span>
            ) : null}
          </div>
        ) : null}
        {row.pipeline_detail ? (
          <span className="queue-row__detail" style={{ fontSize: '0.75rem', color: 'var(--color-text-muted, #888)', marginTop: '2px', display: 'block' }}>
            {row.pipeline_detail}
          </span>
        ) : null}
      </div>
      <div className="queue-row__meta">
        <span className="queue-row__profile">{row.profile_id}</span>
        <span className="queue-row__size">{formatBytes(row.size_bytes)}</span>
      </div>
    </div>
  )
}

const FILTER_TABS = [
  { label: 'All', value: 'all' },
  { label: 'Queued', value: 'queued' },
  { label: 'Transferring', value: 'transferring' },
  { label: 'Ready', value: 'ready' },
  { label: 'Failed', value: 'failed' },
]

export function QueueScreen() {
  const [activeFilter, setActiveFilter] = useState('all')

  const queueQuery = useQuery({
    queryKey: ['queue'],
    queryFn: () => getQueue(),
    staleTime: 10_000,
    refetchInterval: 15_000,
  })

  const rows = queueQuery.data?.rows ?? []

  const counts = useMemo(() => {
    const transferring = rows.filter((r) => r.pipeline_status === 'transferring').length
    const queued = rows.filter((r) => r.pipeline_status === 'queued').length
    const ready = rows.filter((r) => r.pipeline_status === 'ready').length
    return { transferring, queued, ready }
  }, [rows])

  const subtitle = [
    counts.transferring > 0 ? `${counts.transferring} transferring` : null,
    counts.queued > 0 ? `${counts.queued} queued` : null,
    counts.ready > 0 ? `${counts.ready} ready` : null,
  ]
    .filter(Boolean)
    .join(' · ') || 'No active transfers'

  const filtered = useMemo(() => {
    const list =
      activeFilter === 'all'
        ? [...rows]
        : rows.filter((r) => r.pipeline_status === activeFilter)
    return list.sort(
      (a, b) =>
        (PIPELINE_SORT_ORDER[a.pipeline_status] ?? 99) -
        (PIPELINE_SORT_ORDER[b.pipeline_status] ?? 99),
    )
  }, [rows, activeFilter])

  const grouped = useMemo(() => {
    const map = new Map<string, QueueRow[]>()
    for (const row of filtered) {
      const key = parseShowGroup(row.filename)
      const group = map.get(key) ?? []
      group.push(row)
      map.set(key, group)
    }
    return map
  }, [filtered])

  return (
    <section className="screen">
      <header className="screen-header">
        <div>
          <div className="section-label">Queue</div>
          <h2 className="screen-title">Transfer Status</h2>
          <p className="screen-subtitle">{subtitle}</p>
        </div>
      </header>

      <div className="card">
        <div className="library-toolbar">
          <PillTabs tabs={FILTER_TABS} active={activeFilter} onChange={setActiveFilter} />
        </div>

        {queueQuery.isLoading ? (
          <div className="notice">Loading…</div>
        ) : queueQuery.error ? (
          <div className="notice notice--error">{(queueQuery.error as Error).message}</div>
        ) : filtered.length === 0 ? (
          <div className="notice">
            {activeFilter === 'all'
              ? 'No assets yet. Subscribe to content in the Library.'
              : `No ${activeFilter} transfers.`}
          </div>
        ) : (
          <div className="queue-list">
            {Array.from(grouped.entries()).map(([groupName, groupRows]) => (
              <div key={groupName} className="queue-group">
                <div className="queue-group__header">{groupName}</div>
                {groupRows.map((row) => (
                  <QueueRowItem key={`${row.asset_id}-${row.client_id}`} row={row} />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
```

- [ ] **Step 2: TypeScript check**

```bash
cd ui
npm run typecheck
```

Expected: no errors.

- [ ] **Step 3: Lint**

```bash
cd ui
npm run lint
```

Expected: no errors (fix any that appear).

- [ ] **Step 4: Commit**

```bash
git add ui/src/screens/QueueScreen.tsx
git commit -m "feat: QueueScreen uses GET /queue with pipeline_status badges and rate/ETA"
```

---

## Task 11: Library Pills Pipeline Status

**Files:**
- Modify: `ui/src/screens/LibraryScreen.tsx`

Switch pill rendering from `state` to `pipeline_status`. Update the data map to store full `ClientAssignment` objects. Update `episodePillClassName` to map `PipelineStatus`. Update `BulkSyncPill` to aggregate child `pipeline_status` values.

Key changes:
- `ClientAssignmentStateMap` → `ClientAssignmentMap = Map<clientId, Map<mediaItemId, ClientAssignment>>`
- `episodePillClassName` maps `PipelineStatus` → CSS class
- `BulkSyncPill` receives `childMediaItemIds: string[]` and aggregates
- `BulkSyncPills` (the wrapper) passes the children's media item IDs down

- [ ] **Step 1: Update `useClientAssignmentMap` and types at top of `LibraryScreen.tsx`**

Find the type declarations near the top of `LibraryScreen.tsx` and replace:

```typescript
// OLD:
type AssignmentState = ClientAssignment['state']
type ClientAssignmentStateMap = Map<string, Map<string, AssignmentState>>

// NEW:
type ClientAssignmentMap = Map<string, Map<string, ClientAssignment>>
```

Replace `episodePillClassName`:

```typescript
function episodePillClassName(status: PipelineStatus | null): string {
  if (status === 'ready') return 'sync-pill sync-pill--on'
  if (status === 'transferring' || status === 'queued') return 'sync-pill sync-pill--pending'
  if (status === 'failed') return 'sync-pill sync-pill--failed'
  return 'sync-pill'
}
```

(If `.sync-pill--failed` CSS class doesn't exist, use `sync-pill sync-pill--pending` as the fallback.)

Replace `assignmentStateFor`:

```typescript
function assignmentFor(
  assignmentMap: ClientAssignmentMap,
  clientId: string,
  mediaItemId: string,
): ClientAssignment | null {
  return assignmentMap.get(clientId)?.get(mediaItemId) ?? null
}
```

Replace `useClientAssignmentMap`:

```typescript
function useClientAssignmentMap(
  clients: Client[],
  mediaItemIds: string[],
  enabled: boolean,
): ClientAssignmentMap {
  const queries = useQueries({
    queries: clients.map((client) => ({
      queryKey: ['clientAssignments', client.id, mediaItemIds],
      queryFn: () => getClientAssignments(client.id, mediaItemIds),
      enabled: enabled && mediaItemIds.length > 0,
      staleTime: 30_000,
    })),
  })

  return useMemo(() => {
    const map: ClientAssignmentMap = new Map()
    for (const [index, client] of clients.entries()) {
      const clientMap = new Map<string, ClientAssignment>()
      for (const assignment of queries[index]?.data ?? []) {
        clientMap.set(assignment.media_item_id, assignment)
      }
      map.set(client.id, clientMap)
    }
    return map
  }, [clients, queries])
}
```

- [ ] **Step 2: Update `EpisodeSyncPill` to use `pipeline_status`**

Find the `EpisodeSyncPill` component. Change the `currentState` prop type and usage:

```typescript
function EpisodeSyncPill({
  client,
  mediaItemId,
  currentAssignment,  // renamed from currentState
  profiles,
}: {
  client: Client
  mediaItemId: string
  currentAssignment: ClientAssignment | null  // was: AssignmentState | null
  profiles: Profile[]
}) {
  // ... existing subscription mutation logic unchanged ...

  const currentStatus = currentAssignment?.pipeline_status ?? null
  const showForm = pickerOpen && currentAssignment === null
  // ... rest of component: replace currentState with currentStatus for styling ...
  
  // In the rendered pill:
  className={episodePillClassName(currentStatus)}
  disabled={currentAssignment?.state === 'evict' || isBusy}
  // onClick logic: replace currentState === null check with currentAssignment === null
```

Find every call site of `EpisodeSyncPill` in `LibraryScreen.tsx` and update the prop name from `currentState` to `currentAssignment`, passing `assignmentFor(assignmentMap, client.id, mediaItemId)` instead of `assignmentStateFor(...)`.

- [ ] **Step 3: Update `BulkSyncPill` aggregation**

Add the `childMediaItemIds` prop and aggregation logic to `BulkSyncPill`:

```typescript
function BulkSyncPill({
  client,
  showId,
  scopeType,
  scopeParams,
  profiles,
  childMediaItemIds,  // NEW
  assignmentMap,       // NEW
}: {
  client: Client
  showId: string
  scopeType: 'show:all' | 'show:seasons'
  scopeParams: Record<string, unknown> | null
  profiles: Profile[]
  childMediaItemIds: string[]        // NEW
  assignmentMap: ClientAssignmentMap  // NEW
}) {
  // ... existing subscription query + mutation logic unchanged ...

  // NEW: aggregate pipeline_status over children
  const bulkPipelineStatus = useMemo((): PipelineStatus | null => {
    if (!isSubscribed) return null
    const statuses = childMediaItemIds
      .map((id) => assignmentMap.get(client.id)?.get(id)?.pipeline_status)
      .filter((s): s is PipelineStatus => s != null)
    if (statuses.length === 0) return 'queued'
    if (statuses.includes('failed')) return 'failed'
    if (statuses.includes('transferring')) return 'transferring'
    if (statuses.includes('queued')) return 'queued'
    return 'ready'
  }, [childMediaItemIds, assignmentMap, client.id, isSubscribed])

  // Use bulkPipelineStatus for pill color:
  const pillClassName = episodePillClassName(bulkPipelineStatus)
  // ... in the rendered element: use pillClassName instead of the existing isSubscribed/isCoveredByShowAll checks for color
```

Find the `BulkSyncPills` wrapper component and update it to:
1. Accept `childMediaItemIds: string[]` and `assignmentMap: ClientAssignmentMap` props
2. Pass them through to each `BulkSyncPill`

Find the call sites of `BulkSyncPills` in `LibraryScreen.tsx` and pass the current show's episode `mediaItemIds` array and the `assignmentMap`.

- [ ] **Step 4: TypeScript check**

```bash
cd ui
npm run typecheck
```

Expected: no errors. Fix any type errors that appear (typically prop name changes missed at a call site).

- [ ] **Step 5: Lint**

```bash
cd ui
npm run lint
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add ui/src/screens/LibraryScreen.tsx
git commit -m "feat: Library pills use pipeline_status; BulkSyncPill aggregates child statuses"
```

---

## Task 12: Manual Verification

**Goal:** Walk the acceptance criteria (spec §12) against the live server + caravan-pi.

- [ ] **Step 1: Deploy updated server**

On `docker-host01`:

```bash
cd ~/stacks && git pull && docker stack deploy -c syncarr.yml syncarr
```

Wait for the container to restart and apply migration:

```bash
docker service logs syncarr_server --follow 2>&1 | grep -E "alembic|Uvicorn"
```

Expected: `alembic upgrade head` completes, server starts.

- [ ] **Step 2: Build and deploy updated UI**

On the build machine:

```bash
cd local-media-cache/ui && npm run build
```

Copy `dist/` into the Docker image / static path (per your deploy process).

- [ ] **Step 3: Verify no `ready` badge while in-flight**

1. Subscribe to a new episode on caravan-pi.
2. Open Queue screen. Confirm: badge is `queued` (waiting to transcode) or `syncing` (transferring), NOT `ready`.
3. Wait for transfer. Confirm: badge transitions `queued → syncing → ready` in that order.
4. At no point should `ready` appear before the agent confirms delivery.

- [ ] **Step 4: Verify rate + ETA visible during transfer**

While a transfer is active, confirm:
- "X MB / Y MB" progress label visible
- "Z MB/s · ETA N" visible beside the progress label
- Both disappear when `stalled` or `verifying`

- [ ] **Step 5: Verify Library pills match Queue status**

Open Library screen, expand a show with active assignments. Compare the season/episode pills against the Queue screen for the same items. Statuses must match.

- [ ] **Step 6: Record results**

Create `Obsidian/Lab/syncarr/learnings-2026-05-08-pipeline-status.md` with: ✅ pass / ❌ fail for each acceptance criterion from spec §12, plus any bugs found.

---

## Self-Review

### Spec coverage

| Spec section | Covered by |
|---|---|
| §4 Status taxonomy | Task 4 truth table |
| §5.1 Single projection function | Task 4 `pipeline.py` |
| §5.2 Decision truth table (all 12 rows) | Task 4 `test_pipeline.py` |
| §5.3 Stalled detection + update rules | Tasks 4, 5 |
| §5.4 Agent offline detection | Task 4 |
| §5.5 Transfer rate + ETA | Tasks 3, 4 |
| §5.6 Multi-profile support | Tasks 7, 8, 9 |
| §5.7 Confirm-error signal | Tasks 6, 4 |
| §6.1 GET /api/queue | Task 7 |
| §6.2 GET /clients/{id}/assignments additive | Task 8 |
| §6.4 Schema additions (migration) | Tasks 1, 2 |
| §7.1 QueueScreen redesign | Task 10 |
| §7.2 Library pills + BulkSyncPill | Task 11 |
| §8.1 Unit tests truth table | Task 4 |
| §8.2 Integration tests GET /queue | Task 7 |
| §8.3 Progress-writer + confirm-error tests | Tasks 5, 6 |
| §8.5 Manual verification | Task 12 |
| §9 Migration rollout | Task 1 |
| §12 Acceptance criteria | Tasks 1–12 |

**Note on §8.4 (UI unit tests):** The UI project has no test runner installed (no vitest/jest in `package.json`). The UI verification is covered by TypeScript compilation (`npm run typecheck`) in Tasks 9–11 and manual verification in Task 12. Installing a UI test runner is a separate setup task not included here.

### Placeholder scan

No TBD, TODO, or "similar to Task N" references found.

### Type consistency check

- `PipelineProjection` fields used in Task 7 (`p.status`, `p.substate`, `p.detail`, etc.) match the `@dataclass` defined in Task 4.
- `QueueRowSchema.pipeline_status: str` — accepts `PipelineStatus` literals at runtime; mypy strict will accept `str` since `PipelineStatus = Literal[...]` is a subtype.
- `ClientAssignmentSchema` construction in Task 8 uses `p.status or "queued"` — safe since `project()` always returns a non-None status when `visible=True`.
- `ClientAssignmentMap = Map<string, Map<string, ClientAssignment>>` in Task 11 matches the type added to `types.ts` in Task 9.
- `assignmentFor()` in Task 11 replaces `assignmentStateFor()` — all call sites updated in the same task.
- `RateSample` imported from `pipeline.py` in both `rate_tracker.py` (Task 3) and `agent.py` (Task 5) — single definition, no duplication.
