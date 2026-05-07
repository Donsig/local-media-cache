"""Tests for aria2_client.py — patches aria2p.Client and aria2p.API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import aria2p
from syncarr_agent.aria2_client import Aria2Client, DownloadStatus


def _make_mock_download(status: str = "active") -> MagicMock:
    dl = MagicMock()
    dl.status = status
    dl.gid = "abc123"
    dl.completed_length = 1024
    dl.total_length = 2048
    return dl


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_constructor_normalizes_bare_host_to_http(
    mock_client_cls: MagicMock, mock_api_cls: MagicMock
) -> None:
    Aria2Client("127.0.0.1", 6800, "")
    mock_client_cls.assert_called_once_with(host="http://127.0.0.1", port=6800, secret=None)


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_constructor_preserves_existing_scheme(
    mock_client_cls: MagicMock, mock_api_cls: MagicMock
) -> None:
    Aria2Client("http://localhost", 6800, "")
    mock_client_cls.assert_called_once_with(host="http://localhost", port=6800, secret=None)


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_constructor_passes_secret(
    mock_client_cls: MagicMock, mock_api_cls: MagicMock
) -> None:
    Aria2Client("127.0.0.1", 6800, "mysecret")
    mock_client_cls.assert_called_once_with(host="http://127.0.0.1", port=6800, secret="mysecret")


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_add_download_passes_auth_header(
    mock_client_cls: MagicMock, mock_api_cls: MagicMock
) -> None:
    mock_dl = _make_mock_download()
    mock_dl.gid = "gid1"
    mock_api_instance = MagicMock()
    mock_api_instance.add_uris.return_value = mock_dl
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    client.add_download(
        "http://server/download/1",
        "file.mkv",
        Path("/lib/1"),
        "sha256abc",
        "mytoken",
    )

    _args, kwargs = mock_api_instance.add_uris.call_args
    options = kwargs["options"] if "options" in kwargs else _args[1]
    assert options["header"] == "Authorization: Bearer mytoken"


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_add_download_passes_checksum(
    mock_client_cls: MagicMock, mock_api_cls: MagicMock
) -> None:
    mock_dl = _make_mock_download()
    mock_dl.gid = "gid2"
    mock_api_instance = MagicMock()
    mock_api_instance.add_uris.return_value = mock_dl
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    client.add_download(
        "http://server/download/2",
        "file.mkv",
        Path("/lib/2"),
        "deadbeef",
        "tok",
    )

    _args, kwargs = mock_api_instance.add_uris.call_args
    options = kwargs["options"] if "options" in kwargs else _args[1]
    assert options["checksum"] == "sha-256=deadbeef"


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_add_download_disables_auto_rename(
    mock_client_cls: MagicMock, mock_api_cls: MagicMock
) -> None:
    mock_dl = _make_mock_download()
    mock_dl.gid = "gid3"
    mock_api_instance = MagicMock()
    mock_api_instance.add_uris.return_value = mock_dl
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    client.add_download(
        "http://server/download/3",
        "file.mkv",
        Path("/lib/3"),
        "sha256",
        "tok",
    )

    _args, kwargs = mock_api_instance.add_uris.call_args
    options = kwargs["options"] if "options" in kwargs else _args[1]
    assert options["auto-file-renaming"] == "false"


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_get_status_active(mock_client_cls: MagicMock, mock_api_cls: MagicMock) -> None:
    mock_api_instance = MagicMock()
    mock_api_instance.get_download.return_value = _make_mock_download("active")
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    info = client.get_status("gid1")
    assert info.status == DownloadStatus.ACTIVE


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_get_status_waiting(mock_client_cls: MagicMock, mock_api_cls: MagicMock) -> None:
    mock_api_instance = MagicMock()
    mock_api_instance.get_download.return_value = _make_mock_download("waiting")
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    info = client.get_status("gid1")
    assert info.status == DownloadStatus.WAITING


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_get_status_complete(mock_client_cls: MagicMock, mock_api_cls: MagicMock) -> None:
    mock_api_instance = MagicMock()
    mock_api_instance.get_download.return_value = _make_mock_download("complete")
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    info = client.get_status("gid1")
    assert info.status == DownloadStatus.COMPLETE


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_get_status_error(mock_client_cls: MagicMock, mock_api_cls: MagicMock) -> None:
    mock_api_instance = MagicMock()
    mock_api_instance.get_download.return_value = _make_mock_download("error")
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    info = client.get_status("gid1")
    assert info.status == DownloadStatus.ERROR


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_get_status_not_found_returns_other(
    mock_client_cls: MagicMock, mock_api_cls: MagicMock
) -> None:
    mock_api_instance = MagicMock()
    mock_api_instance.get_download.side_effect = aria2p.ClientException(
        1, "GID#stale is not found"
    )
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    info = client.get_status("stale-gid")
    assert info.status == DownloadStatus.OTHER


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_remove_ignores_not_found_error(
    mock_client_cls: MagicMock, mock_api_cls: MagicMock
) -> None:
    mock_api_instance = MagicMock()
    mock_dl = MagicMock()
    mock_dl.remove.side_effect = aria2p.ClientException(1, "GID#abc is not found")
    mock_api_instance.get_download.return_value = mock_dl
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    # Should not raise
    client.remove("abc")


@patch("syncarr_agent.aria2_client.aria2p.API")
@patch("syncarr_agent.aria2_client.aria2p.Client")
def test_remove_raises_on_rpc_error(
    mock_client_cls: MagicMock, mock_api_cls: MagicMock
) -> None:
    mock_api_instance = MagicMock()
    mock_dl = MagicMock()
    mock_dl.remove.side_effect = aria2p.ClientException(1, "Internal server error")
    mock_api_instance.get_download.return_value = mock_dl
    mock_api_cls.return_value = mock_api_instance

    client = Aria2Client("127.0.0.1", 6800, "")
    with pytest.raises(aria2p.ClientException):
        client.remove("abc")
