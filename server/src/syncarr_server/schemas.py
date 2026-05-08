from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MediaLibrarySchema(BaseModel):
    id: str
    title: str
    type: str


class MediaLibrariesResponse(BaseModel):
    libraries: list[MediaLibrarySchema]


class MediaItemSchema(BaseModel):
    id: str
    title: str
    type: str
    year: int | None = None
    file_path: str | None = None
    size_bytes: int | None = None
    parent_id: str | None = None
    season_number: int | None = None
    episode_number: int | None = None


class MediaLibraryItemsResponse(BaseModel):
    items: list[MediaItemSchema]


class MediaItemDetailsResponse(BaseModel):
    item: MediaItemSchema
    children: list[MediaItemSchema]


class MediaPreviewResponse(BaseModel):
    item_id: str
    file_count: int
    total_source_size_bytes: int
    estimated_transcoded_size_bytes: int | None = None


SubscriptionScopeType = Literal["movie", "episode", "show:all", "show:seasons"]


def validate_subscription_scope(
    scope_type: SubscriptionScopeType,
    scope_params: dict[str, object] | None,
) -> None:
    if scope_type in {"movie", "episode", "show:all"}:
        if scope_params is not None:
            raise ValueError(f"scope_params must be null for {scope_type}")
        return

    if scope_params is None or set(scope_params) != {"seasons"}:
        raise ValueError("show:seasons requires scope_params={'seasons': [...]}")

    seasons = scope_params["seasons"]
    if not isinstance(seasons, list) or not seasons:
        raise ValueError("show:seasons requires a non-empty seasons list")
    if not all(isinstance(season, int) and season > 0 for season in seasons):
        raise ValueError("show:seasons seasons must contain positive integers")


class ClientSchema(Schema):
    id: str
    name: str
    storage_budget_bytes: int | None = None
    last_seen: datetime | None = None
    created_at: datetime
    decommissioning: bool


class ClientCreateRequest(Schema):
    id: str
    name: str
    storage_budget_bytes: int | None = None


class ClientCreateResponse(ClientSchema):
    auth_token: str


class ClientUpdateRequest(Schema):
    name: str | None = None
    storage_budget_bytes: int | None = None


class ClientsResponse(Schema):
    clients: list[ClientSchema]


class ProfileSchema(Schema):
    id: str
    name: str
    ffmpeg_args: list[str] | None = None
    target_size_bytes: int | None = None
    created_at: datetime


class ProfileCreateRequest(Schema):
    id: str
    name: str
    ffmpeg_args: list[str] | None = None
    target_size_bytes: int | None = None


class ProfileUpdateRequest(Schema):
    name: str | None = None
    ffmpeg_args: list[str] | None = None
    target_size_bytes: int | None = None


class ProfilesResponse(Schema):
    profiles: list[ProfileSchema]


class _SubscriptionPayload(Schema):
    media_item_id: str
    scope_type: SubscriptionScopeType
    scope_params: dict[str, object] | None = None
    profile_id: str

    @model_validator(mode="after")
    def validate_scope(self) -> _SubscriptionPayload:
        validate_subscription_scope(self.scope_type, self.scope_params)
        return self


class SubscriptionCreateRequest(_SubscriptionPayload):
    client_id: str


class SubscriptionUpdateRequest(Schema):
    media_item_id: str | None = None
    scope_type: SubscriptionScopeType | None = None
    scope_params: dict[str, object] | None = None
    profile_id: str | None = None


class SubscriptionSchema(Schema):
    id: int
    client_id: str
    media_item_id: str
    scope_type: SubscriptionScopeType
    scope_params: dict[str, object] | None = None
    profile_id: str
    created_at: datetime


class SubscriptionsResponse(Schema):
    subscriptions: list[SubscriptionSchema]


class AssetStatusSchema(Schema):
    asset_id: int
    media_item_id: str
    profile_id: str
    filename: str
    status: str
    status_detail: str | None = None
    size_bytes: int | None = None
    ready_at: datetime | None = None
    bytes_downloaded: int | None = None


AgentAssignmentState = Literal["queued", "ready", "evict"]
AgentConfirmState = Literal["delivered", "evicted"]
AgentConfirmMismatchReason = Literal["checksum_mismatch", "size_mismatch"]


class ClientAssignmentSchema(Schema):
    media_item_id: str
    state: AgentAssignmentState
    asset_id: int | None = None
    profile_id: str | None = None
    pipeline_status: str | None = None
    pipeline_substate: str | None = None
    pipeline_detail: str | None = None


class AgentAssignmentSchema(Schema):
    asset_id: int
    state: AgentAssignmentState
    source_media_id: str
    relative_path: str
    size_bytes: int | None = None
    sha256: str | None = None
    download_url: str | None = None


class AgentAssignmentsStats(Schema):
    total_assigned_bytes: int
    ready_count: int
    queued_count: int
    evict_count: int


class AgentAssignmentsResponse(Schema):
    client_id: str
    server_time: datetime
    assignments: list[AgentAssignmentSchema]
    stats: AgentAssignmentsStats


class AgentConfirmRequest(Schema):
    state: AgentConfirmState
    actual_sha256: str | None = None
    actual_size_bytes: int | None = None


class AgentProgressRequest(Schema):
    bytes_downloaded: int = Field(..., ge=0)


class AgentConfirmResponse(Schema):
    ok: bool
    reason: AgentConfirmMismatchReason | None = None
    expected_sha256: str | None = None
    actual_sha256: str | None = None


class ReconcileRequest(Schema):
    assets_present: list[int]
    total_bytes: int = 0


class ReconcileResponse(Schema):
    orphans_to_delete: list[int]
    missing_to_redownload: list[int]


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
