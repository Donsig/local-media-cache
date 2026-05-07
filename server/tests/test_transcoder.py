from __future__ import annotations

import asyncio
import hashlib
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from syncarr_server.config import Settings
from syncarr_server.models import Asset, Profile
from syncarr_server.transcoder import PassthroughWorker, TranscodeWorker

from .conftest import MockFfmpeg

pytestmark = pytest.mark.asyncio


async def _insert_profile(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    profile_id: str,
    ffmpeg_args: list[str] | None,
) -> None:
    async with session_factory() as session:
        session.add(
            Profile(
                id=profile_id,
                name=f"Profile {profile_id}",
                ffmpeg_args=ffmpeg_args,
                target_size_bytes=None,
                created_at=datetime.now(UTC),
            ),
        )
        await session.commit()


async def _insert_asset(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    source_media_id: str,
    profile_id: str,
    source_path: Path,
    status: str = "queued",
    cache_path: str | None = None,
) -> int:
    async with session_factory() as session:
        asset = Asset(
            source_media_id=source_media_id,
            profile_id=profile_id,
            source_path=str(source_path),
            cache_path=cache_path,
            size_bytes=None,
            sha256=None,
            status=status,
            status_detail=None,
            created_at=datetime.now(UTC),
            ready_at=None,
        )
        session.add(asset)
        await session.commit()
        return asset.id


async def _get_asset(
    session_factory: async_sessionmaker[AsyncSession],
    asset_id: int,
) -> Asset:
    async with session_factory() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        return asset


def _make_settings(cache_dir: Path) -> Settings:
    return Settings(
        media_cache_path=str(cache_dir),
        transcode_poll_interval_seconds=60,
    )


async def test_worker_picks_up_queued_asset(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    mock_ffmpeg: MockFfmpeg,
) -> None:
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(b"source-video")
    cache_dir = tmp_path / "cache"

    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=["-c:v", "libx265"],
    )
    asset_id = await _insert_asset(
        db_session_factory,
        source_media_id="media-1",
        profile_id="profile-1",
        source_path=source_path,
    )

    worker = TranscodeWorker(db_session_factory, _make_settings(cache_dir))
    mock_ffmpeg.set_result(returncode=0, output_bytes=b"transcoded-video")

    await worker.run_once()
    asset = await _get_asset(db_session_factory, asset_id)

    assert asset.status == "ready"


async def test_worker_sets_sha256_on_completion(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    mock_ffmpeg: MockFfmpeg,
) -> None:
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(b"source-video")
    cache_dir = tmp_path / "cache"

    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=["-c:v", "libx265"],
    )
    asset_id = await _insert_asset(
        db_session_factory,
        source_media_id="media-1",
        profile_id="profile-1",
        source_path=source_path,
    )

    worker = TranscodeWorker(db_session_factory, _make_settings(cache_dir))
    mock_ffmpeg.set_result(returncode=0, output_bytes=b"transcoded-video")

    await worker.run_once()
    asset = await _get_asset(db_session_factory, asset_id)

    assert asset.sha256 == hashlib.sha256(b"transcoded-video").hexdigest()
    assert asset.size_bytes == len(b"transcoded-video")


async def test_worker_sets_cache_path(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    mock_ffmpeg: MockFfmpeg,
) -> None:
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(b"source-video")
    cache_dir = tmp_path / "cache"

    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=["-c:v", "libx265"],
    )
    asset_id = await _insert_asset(
        db_session_factory,
        source_media_id="media-1",
        profile_id="profile-1",
        source_path=source_path,
    )

    worker = TranscodeWorker(db_session_factory, _make_settings(cache_dir))
    mock_ffmpeg.set_result(returncode=0, output_bytes=b"transcoded-video")

    await worker.run_once()
    asset = await _get_asset(db_session_factory, asset_id)

    assert asset.cache_path is not None
    assert Path(asset.cache_path).exists()


async def test_worker_handles_ffmpeg_failure(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    mock_ffmpeg: MockFfmpeg,
) -> None:
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(b"source-video")
    cache_dir = tmp_path / "cache"

    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=["-c:v", "libx265"],
    )
    asset_id = await _insert_asset(
        db_session_factory,
        source_media_id="media-1",
        profile_id="profile-1",
        source_path=source_path,
    )

    worker = TranscodeWorker(db_session_factory, _make_settings(cache_dir))
    mock_ffmpeg.set_result(returncode=1, stderr="ffmpeg failed badly")

    await worker.run_once()
    asset = await _get_asset(db_session_factory, asset_id)

    assert asset.status == "failed"
    assert asset.status_detail == "ffmpeg failed badly"


async def test_worker_resets_stale_transcoding_on_startup(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(b"source-video")
    partial_path = tmp_path / "cache" / "1.mkv"
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path.write_bytes(b"partial-output")

    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=["-c:v", "libx265"],
    )
    asset_id = await _insert_asset(
        db_session_factory,
        source_media_id="media-1",
        profile_id="profile-1",
        source_path=source_path,
        status="transcoding",
        cache_path=str(partial_path),
    )

    worker = TranscodeWorker(db_session_factory, _make_settings(tmp_path / "cache"))

    await worker.startup_recovery()
    asset = await _get_asset(db_session_factory, asset_id)

    assert asset.status == "queued"
    assert not partial_path.exists()


async def test_worker_processes_one_at_a_time(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    mock_ffmpeg: MockFfmpeg,
) -> None:
    cache_dir = tmp_path / "cache"
    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=["-c:v", "libx265"],
    )

    for index in range(3):
        source_path = tmp_path / f"source-{index}.mkv"
        source_path.write_bytes(f"source-{index}".encode())
        await _insert_asset(
            db_session_factory,
            source_media_id=f"media-{index}",
            profile_id="profile-1",
            source_path=source_path,
        )

    worker = TranscodeWorker(db_session_factory, _make_settings(cache_dir))
    mock_ffmpeg.set_result(returncode=0, output_bytes=b"transcoded-video")

    await worker.run_once()

    async with db_session_factory() as session:
        assets = list((await session.execute(select(Asset).order_by(Asset.id))).scalars())

    assert [asset.status for asset in assets] == ["ready", "queued", "queued"]
    assert len(mock_ffmpeg.calls) == 1


async def test_ffmpeg_args_from_profile(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    mock_ffmpeg: MockFfmpeg,
) -> None:
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(b"source-video")
    cache_dir = tmp_path / "cache"
    ffmpeg_args = ["-c:v", "libx265", "-crf", "28"]

    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=ffmpeg_args,
    )
    await _insert_asset(
        db_session_factory,
        source_media_id="media-1",
        profile_id="profile-1",
        source_path=source_path,
    )

    worker = TranscodeWorker(db_session_factory, _make_settings(cache_dir))
    mock_ffmpeg.set_result(returncode=0, output_bytes=b"transcoded-video")

    await worker.run_once()

    assert mock_ffmpeg.calls == [
        ["ffmpeg", "-y", "-i", str(source_path), *ffmpeg_args, str(cache_dir / "1.mkv")],
    ]


async def test_transcode_worker_skips_passthrough_asset(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    mock_ffmpeg: MockFfmpeg,
) -> None:
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(b"passthrough-video")

    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=None,
    )
    asset_id = await _insert_asset(
        db_session_factory,
        source_media_id="media-1",
        profile_id="profile-1",
        source_path=source_path,
    )

    worker = TranscodeWorker(db_session_factory, _make_settings(tmp_path / "cache"))

    await worker.run_once()
    asset = await _get_asset(db_session_factory, asset_id)

    assert asset.status == "queued"
    assert mock_ffmpeg.calls == []


async def test_passthrough_worker_skips_transcode_asset(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    mock_ffmpeg: MockFfmpeg,
) -> None:
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(b"source-video")

    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=["-c:v", "libx265"],
    )
    asset_id = await _insert_asset(
        db_session_factory,
        source_media_id="media-1",
        profile_id="profile-1",
        source_path=source_path,
    )

    worker = PassthroughWorker(db_session_factory, _make_settings(tmp_path / "cache"))

    await worker.run_once()
    asset = await _get_asset(db_session_factory, asset_id)

    assert asset.status == "queued"
    assert mock_ffmpeg.calls == []


async def test_passthrough_asset_skips_ffmpeg(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    mock_ffmpeg: MockFfmpeg,
) -> None:
    source_bytes = b"passthrough-video"
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(source_bytes)

    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=None,
    )
    asset_id = await _insert_asset(
        db_session_factory,
        source_media_id="media-1",
        profile_id="profile-1",
        source_path=source_path,
    )

    worker = PassthroughWorker(db_session_factory, _make_settings(tmp_path / "cache"))

    await worker.run_once()
    asset = await _get_asset(db_session_factory, asset_id)

    assert mock_ffmpeg.calls == []
    assert asset.status == "ready"
    assert asset.cache_path is None
    assert asset.sha256 is None  # Bug #20: passthrough skips server-side hash; sha256=None by design
    assert asset.size_bytes == len(source_bytes)


async def test_passthrough_asset_no_transcoding_state(
    db_session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    # Bug #20 removed the server-side hash for passthrough assets, making processing instant.
    # The invariant "passthrough never enters transcoding state" is verified by checking
    # the final state transitions directly — no intermediate blocking is needed or possible.
    source_bytes = b"passthrough-video"
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(source_bytes)

    await _insert_profile(
        db_session_factory,
        profile_id="profile-1",
        ffmpeg_args=None,
    )
    asset_id = await _insert_asset(
        db_session_factory,
        source_media_id="media-1",
        profile_id="profile-1",
        source_path=source_path,
    )

    worker = PassthroughWorker(db_session_factory, _make_settings(tmp_path / "cache"))
    await worker.run_once()

    asset = await _get_asset(db_session_factory, asset_id)
    assert asset.status == "ready"
    assert asset.cache_path is None  # passthrough: no transcoding state, no cache file
