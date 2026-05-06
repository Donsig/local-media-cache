from __future__ import annotations

import asyncio
import hashlib
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import structlog

from sqlalchemy import JSON, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from syncarr_server.config import Settings
from syncarr_server.models import Asset, Profile


def _stat_and_hash(path: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    file_path = Path(path)
    size_bytes = file_path.stat().st_size

    with file_path.open("rb") as source_file:
        while chunk := source_file.read(1024 * 1024):
            digest.update(chunk)

    return size_bytes, digest.hexdigest()


def _delete_file_if_exists(path: str | None) -> None:
    if path is None:
        return

    try:
        Path(path).unlink()
    except FileNotFoundError:
        return


class _WorkerBase:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.current_task()

        try:
            while not self._stop_event.is_set():
                try:
                    await self.run_once()
                except Exception as exc:
                    structlog.get_logger().error("transcoder.run_once_error", error=str(exc))
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._settings.transcode_poll_interval_seconds,
                    )
                except TimeoutError:
                    continue
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if task is None or task is asyncio.current_task():
            return

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def run_once(self) -> None:
        raise NotImplementedError


class TranscodeWorker(_WorkerBase):
    async def startup_recovery(self) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Asset).where(Asset.status == "transcoding").order_by(Asset.id),
            )
            assets = list(result.scalars())

            for asset in assets:
                _delete_file_if_exists(asset.cache_path)
                asset.status = "queued"
                asset.cache_path = None
                asset.size_bytes = None
                asset.sha256 = None
                asset.status_detail = None
                asset.ready_at = None

            await session.commit()

    async def run_once(self) -> None:
        loop = asyncio.get_running_loop()

        async with self._session_factory() as session:
            asset_id_result = await session.execute(
                select(Asset.id)
                .join(Profile, Asset.profile_id == Profile.id)
                .where(
                    Asset.status == "queued",
                    Profile.ffmpeg_args.isnot(None),
                    Profile.ffmpeg_args != JSON.NULL,
                )
                .order_by(Asset.id)
                .limit(1),
            )
            asset_id = asset_id_result.scalar_one_or_none()
            if asset_id is None:
                return

            claim_result = cast(
                CursorResult[Any],
                await session.execute(
                    update(Asset)
                    .where(Asset.id == asset_id, Asset.status == "queued")
                    .values(status="transcoding"),
                ),
            )
            if claim_result.rowcount != 1:
                await session.rollback()
                return

            asset = await session.get(Asset, asset_id)
            if asset is None:
                await session.rollback()
                return

            profile = await session.get(Profile, asset.profile_id)
            if profile is None:
                raise ValueError(f"Missing profile for asset {asset.id}")

            cache_path = Path(self._settings.media_cache_path) / f"{asset.id}.mkv"

            cache_path.parent.mkdir(parents=True, exist_ok=True)
            asset.cache_path = str(cache_path)
            asset.status_detail = None
            await session.commit()

            source_path = asset.source_path
            ffmpeg_args = list(cast(list[str], profile.ffmpeg_args))

        cmd = ["ffmpeg", "-y", "-i", source_path, *ffmpeg_args, str(cache_path)]
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True),
        )

        if result.returncode == 0:
            size_bytes, sha256 = await loop.run_in_executor(None, _stat_and_hash, str(cache_path))
            async with self._session_factory() as session:
                asset = await session.get(Asset, asset_id)
                if asset is None:
                    return
                asset.status = "ready"
                asset.cache_path = str(cache_path)
                asset.size_bytes = size_bytes
                asset.sha256 = sha256
                asset.status_detail = None
                asset.ready_at = datetime.now(UTC)
                await session.commit()
            return

        await loop.run_in_executor(None, _delete_file_if_exists, str(cache_path))
        async with self._session_factory() as session:
            asset = await session.get(Asset, asset_id)
            if asset is None:
                return
            asset.status = "failed"
            asset.cache_path = None
            asset.size_bytes = None
            asset.sha256 = None
            asset.ready_at = None
            asset.status_detail = result.stderr[-2000:]
            await session.commit()


class PassthroughWorker(_WorkerBase):
    async def run_once(self) -> None:
        loop = asyncio.get_running_loop()

        async with self._session_factory() as session:
            asset_id_result = await session.execute(
                select(Asset.id)
                .join(Profile, Asset.profile_id == Profile.id)
                .where(
                    Asset.status == "queued",
                    or_(Profile.ffmpeg_args.is_(None), Profile.ffmpeg_args == JSON.NULL),
                )
                .order_by(Asset.id)
                .limit(1),
            )
            asset_id = asset_id_result.scalar_one_or_none()
            if asset_id is None:
                return

            claim_result = cast(
                CursorResult[Any],
                await session.execute(
                    update(Asset)
                    .where(Asset.id == asset_id, Asset.status == "queued")
                    .values(status="transcoding"),
                ),
            )
            if claim_result.rowcount != 1:
                await session.rollback()
                return

            asset = await session.get(Asset, asset_id)
            if asset is None:
                await session.rollback()
                return

            source_path = asset.source_path
            await session.commit()

        size_bytes, sha256 = await loop.run_in_executor(
            None,
            _stat_and_hash,
            source_path,
        )

        async with self._session_factory() as session:
            asset = await session.get(Asset, asset_id)
            if asset is None:
                return
            asset.status = "ready"
            asset.cache_path = None
            asset.size_bytes = size_bytes
            asset.sha256 = sha256
            asset.status_detail = None
            asset.ready_at = datetime.now(UTC)
            await session.commit()
