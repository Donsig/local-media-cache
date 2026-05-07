# Stage 7 Unblock — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Bug #17 (stale-GID "failed" loop, primary T2/T3 blocker), fix the resilience script for T2/T3, fix Bug #16 (delivered UI pills), then run the Stage 7 T0–T5 checklist to completion.

**Architecture:** Three targeted code changes (aria2_client.py → get_status safety, reconciler.py → stale-state recovery, stage7 script → state.db wipe) and one small UI fix (ui.py → delivered treated as "ready" in the client assignments view), followed by the existing PowerShell resilience script.

**Tech Stack:** Python 3.12, aria2p, pytest, PowerShell 7.

---

## File Map

| File | Change |
|------|--------|
| `agent/src/syncarr_agent/aria2_client.py` | `get_status()` catches ClientException "not found" → returns OTHER; stale `add_download` test mock noted |
| `agent/tests/test_aria2_client.py` | Fix stale `return_value = [mock_dl]` in add_download tests; add `test_get_status_not_found_returns_other` |
| `agent/src/syncarr_agent/reconciler.py` | Auto-clear stale "failed" state when file absent; ERROR + no-file → delete state (not set_failed) |
| `agent/tests/test_reconciler.py` | Fix `test_ready_aria2_error_sets_failed` (needs file); add two new tests |
| `scripts/stage7_resilience.ps1` | Wipe `state.db` before agent restart in T2 and T3 |
| `server/src/syncarr_server/routes/ui.py` | `list_client_assignments`: treat delivered as "ready" (Bug #16) |

---

### Task 1: Fix aria2_client — get_status() not-found safety + stale mock

**Context:** When aria2 is restarted without a session file it loses all GID records. Calling `get_status()` on a missing GID throws `aria2p.ClientException`. The outer poll loop catches this and logs `agent.poll_error`, but ALL assignments in that poll are skipped. Wrapping in `get_status()` and returning OTHER converts this to a per-asset state-clear (handled correctly by the reconciler's existing OTHER branch). Separately, the `test_aria2_client.py` tests for `add_download` set `return_value = [mock_dl]` (a list), but `aria2_client.py` now does `download = api.add_uris(...); return str(download.gid)` — `.gid` on a list raises AttributeError. Fix both.

**Files:**
- Modify: `agent/src/syncarr_agent/aria2_client.py`
- Modify: `agent/tests/test_aria2_client.py`

- [ ] **Step 1: Write the failing test for not-found GID**

Add to `agent/tests/test_aria2_client.py` after `test_get_status_error`:

```python
@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_get_status_not_found_returns_other(
    mock_client_cls: MagicMock, mock_api_cls: MagicMock
) -> None:
    mock_api_instance = MagicMock()
    mock_api_instance.get_download.side_effect = aria2p.ClientException(1, "GID#stale is not found")
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    info = client.get_status("stale-gid")
    assert info.status == DownloadStatus.OTHER
```

- [ ] **Step 2: Run the new test to confirm it fails**

```bash
cd agent && uv run pytest tests/test_aria2_client.py::test_get_status_not_found_returns_other -v
```

Expected: `FAILED` — `aria2p.ClientException` is not caught, propagates out.

- [ ] **Step 3: Fix get_status() in aria2_client.py**

In `agent/src/syncarr_agent/aria2_client.py`, replace:

```python
    def get_status(self, gid: str) -> DownloadInfo:
        dl = self._api.get_download(gid)
        raw = dl.status
```

With:

```python
    def get_status(self, gid: str) -> DownloadInfo:
        try:
            dl = self._api.get_download(gid)
        except aria2p.ClientException as exc:
            # Only catch "not found" errors — not broad _is_not_found() which also matches
            # "gid#" substring appearing in non-not-found messages. Explicit check avoids
            # silently swallowing real errors.
            msg = str(exc).lower()
            if "not found" in msg:
                return DownloadInfo(gid=gid, status=DownloadStatus.OTHER, completed_length=0, total_length=0)
            raise
        raw = dl.status
```

- [ ] **Step 4: Run the new test to confirm it passes**

```bash
cd agent && uv run pytest tests/test_aria2_client.py::test_get_status_not_found_returns_other -v
```

Expected: `PASSED`

- [ ] **Step 5: Fix stale add_download mock (return_value = [mock_dl] → mock_dl)**

In `agent/tests/test_aria2_client.py`, in EACH of the three `add_download` tests
(`test_add_download_passes_auth_header`, `test_add_download_passes_checksum`,
`test_add_download_disables_auto_rename`), change:

```python
    mock_api_instance.add_uris.return_value = [mock_dl]
```

To:

```python
    mock_api_instance.add_uris.return_value = mock_dl
```

- [ ] **Step 6: Run all aria2_client tests**

```bash
cd agent && uv run pytest tests/test_aria2_client.py -v
```

Expected: all tests `PASSED` (currently the three add_download tests may be erroring with
AttributeError on `[mock_dl].gid`).

- [ ] **Step 7: Commit**

```bash
git add agent/src/syncarr_agent/aria2_client.py agent/tests/test_aria2_client.py
git commit -m "fix(agent): get_status catches missing-GID exception; fix stale mock in add_download tests"
```

---

### Task 2: Fix reconciler — stale-failed auto-clear + ERROR+no-file case

**Context:** Two cases cause permanent stuck state:

1. **Stale "failed" record**: After a previous test run, state.db may contain `status="failed"` for an asset where the file no longer exists (was cleaned up). The reconciler returns early with "operator must clear state.db", skipping the asset forever. Fix: if file absent, auto-clear and let the next poll re-queue.

2. **aria2 ERROR + no file**: When aria2 is restarted without a session file, the GID is gone (returns OTHER, now handled by Task 1). But if somehow ERROR is returned for a GID with no file on disk (e.g., aria2 had a transient download error), calling `set_failed()` permanently blocks re-download. Fix: ERROR + no file → `delete()` state, re-queue on next poll. ERROR + file exists → `set_failed()` (disk-full / IO error, operator intervention required).

**Files:**
- Modify: `agent/src/syncarr_agent/reconciler.py`
- Modify: `agent/tests/test_reconciler.py`

- [ ] **Step 1: Write two new failing tests**

Add to `agent/tests/test_reconciler.py`:

```python
def test_ready_stale_failed_no_file_auto_clears(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """Stale 'failed' record with no file on disk → auto-clear, re-queue next poll."""
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    # File does NOT exist — stale state from prior test run
    _add_record(mock_state, assignment, local_path, "old-gid", status="failed")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    # State cleared; no set_failed, no add_download (re-queue happens next poll)
    assert mock_state.deleted == [assignment.asset_id]
    assert mock_state.set_failed_calls == []
    assert mock_aria2._add_calls == []


def test_ready_aria2_error_no_file_clears_state(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """aria2 ERROR + file absent → clear state (re-queue next poll), not set_failed."""
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    # File does NOT exist
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.ERROR)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.deleted == [assignment.asset_id]
    assert mock_state.set_failed_calls == []
    assert mock_aria2._add_calls == []  # re-queue is on next poll, not this one


def test_ready_failed_with_file_still_skips(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """Failed record + file present → log warning and skip (disk-full scenario)."""
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, b"partial download - disk full scenario")
    _add_record(mock_state, assignment, local_path, "old-gid", status="failed")

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.deleted == []
    assert mock_state.set_failed_calls == []  # already failed, not re-set
    assert mock_aria2._add_calls == []
    assert mock_server.delivered_confirms == []
```

- [ ] **Step 2: Update existing aria2-error test to require file**

Update `test_ready_aria2_error_sets_failed` to create the file before running reconcile,
so it still tests the "file exists + ERROR → set_failed" (disk-full scenario):

Replace:

```python
def test_ready_aria2_error_sets_failed(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.ERROR)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.set_failed_calls == [assignment.asset_id]
    assert mock_server.delivered_confirms == []
```

With:

```python
def test_ready_aria2_error_with_file_sets_failed(
    mock_state,
    mock_aria2,
    mock_server,
    tmp_library_root: Path,
) -> None:
    """aria2 ERROR + file present → set_failed (disk-full / IO error; operator must intervene)."""
    assignment = _assignment()
    local_path = _local_path(tmp_library_root, assignment)
    _write_file(local_path, b"partial download")  # file exists (disk-full scenario)
    _add_record(mock_state, assignment, local_path, "gid001")
    mock_aria2.set_status("gid001", DownloadStatus.ERROR)

    _run_reconcile([assignment], mock_state, mock_aria2, mock_server, tmp_library_root)

    assert mock_state.set_failed_calls == [assignment.asset_id]
    assert mock_server.delivered_confirms == []
```

- [ ] **Step 3: Run tests to confirm failures**

```bash
cd agent && uv run pytest tests/test_reconciler.py -v -k "aria2_error or stale_failed"
```

Expected: `test_ready_stale_failed_no_file_auto_clears` FAILED, `test_ready_aria2_error_no_file_clears_state` FAILED, `test_ready_aria2_error_with_file_sets_failed` may FAIL (no file created in old version).

- [ ] **Step 4: Fix reconciler.py — stale-failed auto-clear**

In `agent/src/syncarr_agent/reconciler.py`, in `_handle_ready`, replace:

```python
        if record.status == "failed":
            log.warning("agent.download_failed_skip", note="operator must clear state.db and free disk")
            return
```

With:

```python
        if record.status == "failed":
            if not local_path.exists():
                log.info("agent.stale_failed_cleared", asset_id=asset_id)
                state.delete(asset_id)
                return  # re-queue on next poll
            log.warning("agent.download_failed_skip", note="disk full — operator must clear state.db and free disk")
            return
```

- [ ] **Step 5: Fix reconciler.py — ERROR + no-file clears state**

In `agent/src/syncarr_agent/reconciler.py`, in `_handle_ready`, replace:

```python
        if info.status == DownloadStatus.ERROR:
            log.warning("agent.download_aria2_error", gid=record.gid)
            state.set_failed(asset_id)
            return
```

With:

```python
        if info.status == DownloadStatus.ERROR:
            log.warning("agent.download_aria2_error", gid=record.gid)
            if local_path.exists():
                state.set_failed(asset_id)
            else:
                state.delete(asset_id)  # stale GID, no file — re-queue next poll
            return
```

- [ ] **Step 6: Run all reconciler tests**

```bash
cd agent && uv run pytest tests/test_reconciler.py -v
```

Expected: all tests `PASSED`. Confirm the two new tests pass and `test_ready_aria2_error_with_file_sets_failed` passes.

- [ ] **Step 7: Run full agent test suite**

```bash
cd agent && uv run pytest -v
```

Expected: all agent tests `PASSED` (no regressions).

- [ ] **Step 8: Guard _confirm_or_requeue against missing file (COMPLETE + no file edge case)**

If a GID transitions through not-found → OTHER (cleared by the Task 1 fix) then somehow reappears as COMPLETE in the next poll, `_confirm_or_requeue` will call `_sha256_file(local_path)` on a file that doesn't exist — raising `FileNotFoundError`, which aborts the whole poll. Add an existence check.

In `agent/src/syncarr_agent/reconciler.py`, in `_confirm_or_requeue`, replace:

```python
    actual_sha = _sha256_file(local_path)
    # sha256=None means passthrough; skip local sha256 verification.
    sha256_ok = assignment.sha256 is None or actual_sha == assignment.sha256
```

With:

```python
    if not local_path.exists():
        log.warning("agent.complete_but_missing", asset_id=asset_id)
        state.delete(asset_id)
        return
    actual_sha = _sha256_file(local_path)
    # sha256=None means passthrough; skip local sha256 verification.
    sha256_ok = assignment.sha256 is None or actual_sha == assignment.sha256
```

- [ ] **Step 9: Run full agent test suite**

```bash
cd agent && uv run pytest -v
```

Expected: all agent tests `PASSED` (no regressions).

- [ ] **Step 10: Commit**

```bash
git add agent/src/syncarr_agent/reconciler.py agent/tests/test_reconciler.py
git commit -m "fix(agent): auto-clear stale 'failed' state and aria2 ERROR with no file — re-queue on next poll"
```

---

### Task 3: Fix stage7 script — wipe state.db before T2/T3 agent restart

**Context:** state.db on the satellite may contain GIDs from the previous test pass (T0–T1 or a prior aborted run). Wiping it before the T2 and T3 agent restarts ensures a clean slate so the reconciler's crash-recovery path (not the stale-GID path) handles the in-progress download.

Note: wiping state.db is safe here because aria2 keeps its own session state (partial .aria2 control files survive). If aria2 has a session file configured (`--save-session`), it will continue downloads. If not, the agent will re-add the download and aria2 will start fresh (file is deleted and re-downloaded, which still proves T2's "restart survives" scenario).

**Files:**
- Modify: `scripts/stage7_resilience.ps1`

- [ ] **Step 1: Wipe state.db BEFORE starting the agent for T2 — not after kill**

**Critical placement:** The wipe must happen before any download is active, not between kill and restart. Wiping after the agent kill but while aria2 is still downloading removes the GID mapping from state.db, causing the reconciler to treat the partial file as an orphan (corrupt crash-recovery) and delete/requeue it rather than polling the still-running aria2 by GID. Also, wiping while the agent is running (even briefly) can cause "no such table: downloads" because `StateDB` only creates the schema in `__init__` — later polls open fresh connections expecting the table to exist.

In `scripts/stage7_resilience.ps1`, in the **T1 block**, before `ssh satellite "systemctl --user start syncarr-agent"` (line 125), add the wipe. At that point: the agent is stopped, aria2 is idle, no download is in flight.

Replace the existing T2 start block (around line 125):

```powershell
ssh satellite "systemctl --user start syncarr-agent"
Info "Agent started -- waiting for aria2 to pick up download..."
```

With:

```powershell
ssh satellite "rm -f ~/media/.syncarr/state.db"
Info "state.db wiped before T2 agent start (clean GID slate)"
ssh satellite "systemctl --user start syncarr-agent"
Info "Agent started -- waiting for aria2 to pick up download..."
```

Do NOT add a wipe between the T2 kill and restart — leave that section unchanged.

- [ ] **Step 2: Remove the T3 state.db wipe entirely**

The plan previously proposed wiping `state.db` before the T3 re-subscribe. **Do not add this wipe.** The T3 agent is still running at that point; deleting the DB file under a live agent can cause "no such table: downloads" on the next poll (StateDB only creates the schema on startup). The correct T3 recovery path is: agent sees stale GID (aria2 restarted without session file), `get_status()` returns OTHER (from Task 1 fix), reconciler clears state, re-queues on next poll. No wipe needed.

- [ ] **Step 3: Verify the edits look correct**

Read the modified sections of `scripts/stage7_resilience.ps1` and confirm the wipes appear in the right places relative to the `systemctl start` and `Invoke-RestMethod` calls.

- [ ] **Step 4: Commit**

```bash
git add scripts/stage7_resilience.ps1
git commit -m "chore(scripts): wipe state.db before agent restart in T2 and before T3 re-subscribe"
```

---

### Task 4: Fix Bug #16 — delivered assignments show as "ready" in UI pills

**Context:** The sync pills in the library tree call `GET /clients/{client_id}/assignments?media_item_ids=...`. After delivery, `assignment.state = "delivered"`, which `_effective_state()` maps to `None`, causing the pill to disappear (show as unsubscribed). Fix: treat "delivered" as "ready" in this UI-specific view. The agent route remains unchanged — it should NOT receive delivered assignments.

**Files:**
- Modify: `server/src/syncarr_server/routes/ui.py`

- [ ] **Step 1: Fix list_client_assignments**

In `server/src/syncarr_server/routes/ui.py`, in `list_client_assignments`, replace:

```python
    for assignment, asset in result.all():
        effective_state = _effective_state(assignment, asset)
        if effective_state is None:
            continue
        assignments.append(
            ClientAssignmentSchema(
                media_item_id=asset.source_media_id,
                state=effective_state,
            )
        )
```

With:

```python
    for assignment, asset in result.all():
        effective_state = _effective_state(assignment, asset)
        if effective_state is None:
            if assignment.state != "delivered":
                continue
            # Delivered = file confirmed on satellite; show as "ready" in UI.
            # Use a separate append rather than reassigning effective_state to avoid
            # mypy strict narrowing complaint (None → str reassignment in Literal context).
            assignments.append(
                ClientAssignmentSchema(
                    media_item_id=asset.source_media_id,
                    state="ready",
                )
            )
            continue
        assignments.append(
            ClientAssignmentSchema(
                media_item_id=asset.source_media_id,
                state=effective_state,
            )
        )
```

- [ ] **Step 2: Add delivered→ready test case to the existing server test**

`server/tests/test_ui_routes.py` already has `test_list_client_assignments` which tests `state="pending"` + asset `status="ready"` → response `state="ready"`. Add a second assertion for the delivered case.

In `server/tests/test_ui_routes.py`, in `test_list_client_assignments` (around line 293), after the existing assignment is added to the DB, add a second delivered assignment for a different asset and assert it also appears as `"ready"`:

```python
# Also test: delivered assignment → appears as "ready" in UI view (Bug #16 fix)
asset2 = Asset(
    source_media_id="ep-delivered",
    profile_id="p1",
    source_path="/mnt/media/ep-delivered.mkv",
    cache_path=None,
    size_bytes=5678,
    sha256="def456",
    status="ready",
    status_detail=None,
    created_at=datetime.now(UTC),
    ready_at=datetime.now(UTC),
)
db_session.add(asset2)
await db_session.flush()

assignment2 = Assignment(
    client_id="caravan",
    asset_id=asset2.id,
    state="delivered",
    created_at=datetime.now(UTC),
    delivered_at=datetime.now(UTC),
    evict_requested_at=None,
)
db_session.add(assignment2)
await db_session.commit()

delivered_response = await http_client.get(
    "/clients/caravan/assignments?media_item_ids=ep-delivered",
    headers=auth_headers_ui,
)
assert delivered_response.status_code == 200
assert delivered_response.json() == [{"media_item_id": "ep-delivered", "state": "ready"}]
```

- [ ] **Step 3: Run server tests**

```bash
cd server && uv run pytest -v
```

Expected: all server tests `PASSED`.

- [ ] **Step 4: Commit**

```bash
git add server/src/syncarr_server/routes/ui.py
git commit -m "fix(ui): show delivered assignments as 'ready' in client assignments view (Bug #16)"
```

---

### Task 5: Run Stage 7 T0–T5 validation

**Context:** All fixes are deployed. Run the resilience script against the live homelab. The server is at `http://192.168.1.176:8000`, the satellite at `192.168.1.41`. T6 (48h offline) and T7 (7-day cruft) require elapsed real time — defer.

**Prerequisites:**
- Docker container running on docker-host01 (`ssh docker-host01 "docker ps"`)
- syncarr-agent active on satellite (`ssh satellite "systemctl --user is-active syncarr-agent"`)
- aria2 active on satellite (`ssh satellite "systemctl --user is-active aria2"`)
- Deploy updated agent to satellite (see below)

- [ ] **Step 1: Deploy updated agent to satellite**

```powershell
ssh satellite "cd ~/syncarr && git pull && uv pip install -e agent/"
ssh satellite "systemctl --user restart syncarr-agent"
ssh satellite "systemctl --user is-active syncarr-agent"
```

Expected: `active`

- [ ] **Step 2: Deploy updated server to docker-host01**

```powershell
ssh docker-host01 "cd ~/syncarr && git pull"
ssh docker-host01 "docker compose -f ~/syncarr/docker-compose.yml up -d --build syncarr"
```

Wait ~30s for the container to restart, then:

```powershell
Invoke-RestMethod "http://192.168.1.176:8000/api/clients" -Headers @{ Authorization = "Bearer test" }
```

Expected: `200 OK` with clients list.

- [ ] **Step 3: Run the resilience script**

```powershell
cd C:\Dev\Repos\OwnGitHub\local-media-cache
.\scripts\stage7_resilience.ps1
```

Monitor the output. All T0–T5 checks should print `PASS [...]` in green. Watch specifically for:
- `T1.ready` — asset becomes ready (PassthroughWorker runs)
- `T2.deliver` — delivery confirmed after agent kill+restart (the Bug #17 fix)
- `T3.deliver` — delivery confirmed after aria2 kill+restart
- `T4.state` — asset still "ready" after server container restart
- `T5.file` — file evicted from satellite

- [ ] **Step 4: If T2 or T3 fail, check agent logs on satellite**

```powershell
ssh satellite "journalctl --user -u syncarr-agent -n 50 --no-pager"
```

Look for `agent.stale_failed_cleared` or `agent.download_stale_gid` log lines — these confirm the new auto-clear paths fired.

If `T2.deliver` times out, check if the agent is looping on `agent.download_failed_skip` (still seeing failed state). If so, manually wipe state.db and restart:

```powershell
ssh satellite "rm -f ~/media/.syncarr/state.db && systemctl --user restart syncarr-agent"
```

- [ ] **Step 5: Update Obsidian index**

In `C:\Dev\Obsidian\Lab\syncarr\index.md`:

1. Change Stage 7 checkbox from `[ ]` to `[x]` with note: `T0–T5 pass`.
2. Add Bug #16 and Bug #17 to the Bugs table as `Fixed`.
3. Update "Next Session" section to point to `/reconcile` endpoint and distributed testing.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: Stage 7 T0-T5 resilience validation complete"
```

---

## Self-Review

**Spec coverage:**

- Bug #17 (T2/T3 blocker): covered by Tasks 1 + 2 (get_status not-found + stale-failed auto-clear + ERROR-no-file)
- Bug #16 (delivered pills): covered by Task 4
- Script fix (state.db wipe): covered by Task 3
- Stage 7 execution: covered by Task 5

**Placeholder scan:** No TBDs, TODOs, or "similar to Task N" references. All code blocks show complete replacements.

**Type consistency:** `DownloadStatus.OTHER` used consistently in Task 1 test and aria2_client fix. `state.delete(asset_id)` matches the `StateDB.delete()` signature in both Task 2 code blocks. `assignment.state` (string) comparison in Task 4 matches the ORM model.

**Edge case: Task 3 T3 wipe timing** — the wipe is placed before re-subscribe, not before aria2 restart. The agent stays running during T3, so its in-memory state is still current; only the disk state.db needs to be consistent for the next session. Since the agent is NOT restarted in T3, the wipe is a no-op for the live agent (it already has state in memory). This is intentional — T3 tests aria2 kill/resume with a running agent. If T3 is still flaky, consider also restarting the agent after the wipe.
