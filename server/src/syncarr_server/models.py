from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import TIMESTAMP


class Base(DeclarativeBase):
    pass


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    auth_token: Mapped[str] = mapped_column(Text, nullable=False)
    storage_budget_bytes: Mapped[int | None] = mapped_column(Integer)
    last_seen: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    decommissioning: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("0"),
        default=False,
    )

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="client")
    assignments: Mapped[list[Assignment]] = relationship(back_populates="client")


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ffmpeg_args: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # None = passthrough: serve source file directly, skip ffmpeg entirely
    target_size_bytes: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="profile")
    assets: Mapped[list[Asset]] = relationship(back_populates="profile")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(Text, ForeignKey("clients.id"), nullable=False)
    media_item_id: Mapped[str] = mapped_column(Text, nullable=False)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_params: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    profile_id: Mapped[str] = mapped_column(Text, ForeignKey("profiles.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)

    client: Mapped[Client] = relationship(back_populates="subscriptions")
    profile: Mapped[Profile] = relationship(back_populates="subscriptions")


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("source_media_id", "profile_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_media_id: Mapped[str] = mapped_column(Text, nullable=False)
    profile_id: Mapped[str] = mapped_column(Text, ForeignKey("profiles.id"), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    cache_path: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    status_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    ready_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)

    profile: Mapped[Profile] = relationship(back_populates="assets")
    assignments: Mapped[list[Assignment]] = relationship(back_populates="asset")


class Assignment(Base):
    __tablename__ = "assignments"

    client_id: Mapped[str] = mapped_column(Text, ForeignKey("clients.id"), primary_key=True)
    asset_id: Mapped[int] = mapped_column(Integer, ForeignKey("assets.id"), primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    evict_requested_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    bytes_downloaded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_downloaded_updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    last_confirm_error_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    last_confirm_error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    client: Mapped[Client] = relationship(back_populates="assignments")
    asset: Mapped[Asset] = relationship(back_populates="assignments")
