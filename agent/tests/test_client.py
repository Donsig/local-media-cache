"""Tests for client.py — uses respx for HTTP mocking."""

from __future__ import annotations

import httpx
import pytest
import respx

from syncarr_agent.client import ServerClient

SERVER_URL = "http://server:8000"

FULL_RESPONSE = {
    "client_id": "caravan",
    "server_time": "2026-05-04T07:30:00Z",
    "assignments": [
        {
            "asset_id": 1234,
            "state": "ready",
            "source_media_id": "12345",
            "relative_path": "Bluey (2018)/Season 2/Bluey - S02E01 - Dance Mode.mkv",
            "size_bytes": 5120000000,
            "sha256": "abc123def456",
            "download_url": "/download/1234",
        },
        {
            "asset_id": 1235,
            "state": "queued",
            "source_media_id": "12346",
            "relative_path": "Bluey (2018)/Season 2/Bluey - S02E02 - Bumpy.mkv",
        },
        {
            "asset_id": 1100,
            "state": "evict",
            "source_media_id": "11000",
            "relative_path": "Bluey (2018)/Season 1/Bluey - S01E01 - Magic Xylophone.mkv",
        },
    ],
    "stats": {
        "total_assigned_bytes": 120000000000,
        "ready_count": 18,
        "queued_count": 6,
        "evict_count": 0,
    },
}


def _make_client(router: respx.MockRouter) -> ServerClient:
    transport = httpx.MockTransport(router.handler)
    return ServerClient(SERVER_URL, "test-token", transport=transport)


def test_get_assignments_parses_full_response() -> None:
    with respx.mock(base_url=SERVER_URL) as router:
        router.get("/assignments").mock(return_value=httpx.Response(200, json=FULL_RESPONSE))
        client = _make_client(router)
        resp = client.get_assignments()

    assert resp.client_id == "caravan"
    assert resp.server_time == "2026-05-04T07:30:00Z"
    assert len(resp.assignments) == 3
    assert resp.stats.ready_count == 18
    assert resp.stats.queued_count == 6
    assert resp.stats.evict_count == 0
    assert resp.stats.total_assigned_bytes == 120000000000


def test_get_assignments_normalizes_download_url() -> None:
    with respx.mock(base_url=SERVER_URL) as router:
        router.get("/assignments").mock(return_value=httpx.Response(200, json=FULL_RESPONSE))
        client = _make_client(router)
        resp = client.get_assignments()

    ready = next(a for a in resp.assignments if a.state == "ready")
    assert ready.download_url == "http://server:8000/download/1234"


def test_get_assignments_handles_missing_optional_fields() -> None:
    with respx.mock(base_url=SERVER_URL) as router:
        router.get("/assignments").mock(return_value=httpx.Response(200, json=FULL_RESPONSE))
        client = _make_client(router)
        resp = client.get_assignments()

    queued = next(a for a in resp.assignments if a.state == "queued")
    assert queued.sha256 is None
    assert queued.size_bytes is None
    assert queued.download_url is None

    evict = next(a for a in resp.assignments if a.state == "evict")
    assert evict.sha256 is None
    assert evict.size_bytes is None
    assert evict.download_url is None


def test_get_assignments_network_error_raises() -> None:
    with respx.mock(base_url=SERVER_URL) as router:
        router.get("/assignments").mock(side_effect=httpx.ConnectError("refused"))
        client = _make_client(router)
        with pytest.raises(httpx.ConnectError):
            client.get_assignments()


def test_confirm_delivered_sends_correct_body() -> None:
    with respx.mock(base_url=SERVER_URL) as router:
        route = router.post("/confirm/42").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = _make_client(router)
        client.confirm_delivered(42, "sha256abc", 1024)

    assert route.called
    body = route.calls.last.request.read()
    import json

    parsed = json.loads(body)
    assert parsed == {
        "state": "delivered",
        "actual_sha256": "sha256abc",
        "actual_size_bytes": 1024,
    }


def test_confirm_delivered_ok() -> None:
    with respx.mock(base_url=SERVER_URL) as router:
        router.post("/confirm/42").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = _make_client(router)
        result = client.confirm_delivered(42, "sha256abc", 1024)

    assert result is True


def test_confirm_delivered_mismatch() -> None:
    with respx.mock(base_url=SERVER_URL) as router:
        router.post("/confirm/42").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": False,
                    "reason": "checksum_mismatch",
                    "expected_sha256": "expected",
                    "actual_sha256": "actual",
                },
            )
        )
        client = _make_client(router)
        result = client.confirm_delivered(42, "actual", 1024)

    assert result is False


def test_confirm_evicted_sends_correct_body() -> None:
    with respx.mock(base_url=SERVER_URL) as router:
        route = router.post("/confirm/99").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = _make_client(router)
        client.confirm_evicted(99)

    assert route.called
    import json

    parsed = json.loads(route.calls.last.request.read())
    assert parsed == {"state": "evicted"}


def test_confirm_evicted_404_treated_as_success() -> None:
    with respx.mock(base_url=SERVER_URL) as router:
        router.post("/confirm/55").mock(return_value=httpx.Response(404))
        client = _make_client(router)
        # Should not raise
        client.confirm_evicted(55)


def test_report_progress_sends_patch(respx_mock: respx.MockRouter) -> None:
    respx_mock.patch("/assignments/7/progress").mock(return_value=httpx.Response(204))
    client = ServerClient("http://server", "tok")
    client.report_progress(7, 512_000)
    assert respx_mock.calls.call_count == 1
    assert respx_mock.calls[0].request.content == b'{"bytes_downloaded":512000}'


def test_report_progress_ignores_errors(respx_mock: respx.MockRouter) -> None:
    respx_mock.patch("/assignments/7/progress").mock(return_value=httpx.Response(500))
    client = ServerClient("http://server", "tok")
    client.report_progress(7, 512_000)


def test_get_assignments_parses_relative_path() -> None:
    with respx.mock(base_url=SERVER_URL) as router:
        router.get("/assignments").mock(return_value=httpx.Response(200, json=FULL_RESPONSE))
        client = _make_client(router)
        resp = client.get_assignments()

    ready = next(a for a in resp.assignments if a.state == "ready")
    assert ready.relative_path == "Bluey (2018)/Season 2/Bluey - S02E01 - Dance Mode.mkv"


def test_get_assignments_rejects_path_traversal() -> None:
    bad_response = {
        **FULL_RESPONSE,
        "assignments": [
            {
                "asset_id": 999,
                "state": "ready",
                "source_media_id": "x",
                "relative_path": "../../etc/passwd",
                "size_bytes": 1,
                "sha256": "abc",
                "download_url": "/download/999",
            }
        ],
    }
    with respx.mock(base_url=SERVER_URL) as router:
        router.get("/assignments").mock(return_value=httpx.Response(200, json=bad_response))
        client = _make_client(router)
        with pytest.raises(ValueError, match="Unsafe"):
            client.get_assignments()
