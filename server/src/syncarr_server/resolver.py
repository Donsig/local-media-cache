from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.models import Asset, Assignment, Client, Subscription
from syncarr_server.providers.base import MediaProvider


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def gc_orphaned_assets(session: AsyncSession) -> None:
    await session.execute(delete(Asset).where(~Asset.assignments.any()))


async def resolve_all_subscriptions(provider: MediaProvider, session: AsyncSession) -> None:
    now = _utc_now()

    async with session.begin():
        subscriptions = list((await session.execute(select(Subscription))).scalars())
        assets_by_key: dict[tuple[str, str], Asset] = {}
        desired_assignments: set[tuple[str, int]] = set()

        for subscription in subscriptions:
            items = provider.expand_scope(
                subscription.media_item_id,
                subscription.scope_type,
                subscription.scope_params,
            )
            for item in items:
                if item.file_path is None:
                    raise ValueError(
                        f"Expanded media item {item.provider_id!r} is missing file_path",
                    )

                asset_key = (item.provider_id, subscription.profile_id)
                asset = assets_by_key.get(asset_key)
                if asset is None:
                    asset = (
                        await session.execute(
                            select(Asset).where(
                                Asset.source_media_id == item.provider_id,
                                Asset.profile_id == subscription.profile_id,
                            ),
                        )
                    ).scalar_one_or_none()
                    if asset is None:
                        asset = Asset(
                            source_media_id=item.provider_id,
                            profile_id=subscription.profile_id,
                            source_path=item.file_path,
                            cache_path=None,
                            size_bytes=None,
                            sha256=None,
                            status="queued",
                            status_detail=None,
                            created_at=now,
                            ready_at=None,
                        )
                        session.add(asset)
                        await session.flush()
                    assets_by_key[asset_key] = asset

                desired_assignments.add((subscription.client_id, asset.id))

        assignments = list((await session.execute(select(Assignment))).scalars())
        active_assignments = {
            (assignment.client_id, assignment.asset_id): assignment
            for assignment in assignments
            if assignment.state in {"pending", "delivered"}
        }
        evicting_assignments = {
            (assignment.client_id, assignment.asset_id): assignment
            for assignment in assignments
            if assignment.state == "evict"
        }

        for client_id, asset_id in desired_assignments:
            assignment_key = (client_id, asset_id)
            if assignment_key in evicting_assignments:
                assignment = evicting_assignments[assignment_key]
                assignment.state = "pending"
                assignment.evict_requested_at = None
            elif assignment_key not in active_assignments:
                session.add(
                    Assignment(
                        client_id=client_id,
                        asset_id=asset_id,
                        state="pending",
                        created_at=now,
                        delivered_at=None,
                        evict_requested_at=None,
                    ),
                )

        for assignment_key, assignment in active_assignments.items():
            if assignment_key not in desired_assignments:
                assignment.state = "evict"
                assignment.evict_requested_at = now

        await gc_orphaned_assets(session)
        await session.execute(
            delete(Client).where(
                Client.decommissioning.is_(True),
                ~Client.assignments.any(),
            ),
        )
