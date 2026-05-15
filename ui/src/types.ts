export type Client = {
  id: string
  name: string
  storage_budget_bytes: number | null
  last_seen: string | null
  created_at: string
  decommissioning: boolean
}

export type ClientCreateResponse = Client & {
  auth_token: string
}

export type Profile = {
  id: string
  name: string
  ffmpeg_args: string[] | null
  target_size_bytes: number | null
  created_at: string
}

export type Subscription = {
  id: number
  client_id: string
  media_item_id: string
  scope_type: string
  scope_params: Record<string, unknown> | null
  profile_id: string
  created_at: string
}

export type ClientAssignment = {
  media_item_id: string
  state: 'ready' | 'queued' | 'evict'
  asset_id: number
  profile_id: string
  pipeline_status: PipelineStatus
  pipeline_substate: PipelineSubstate | null
  pipeline_detail: string | null
}

export type PipelineStatus = 'queued' | 'transferring' | 'ready' | 'failed'
export type TransferMode = 'running' | 'paused' | 'stopped'

export type PipelineSubstate =
  | 'transcoding_pending'
  | 'transcoding'
  | 'paused'
  | 'waiting_for_agent'
  | 'agent_offline'
  | 'downloading'
  | 'verifying'
  | 'stalled'
  | 'delivered'
  | 'transcode_failed'

export type MediaLibrary = {
  id: string
  title: string
  type: string
}

export type MediaItem = {
  id: string
  title: string
  type: string
  year: number | null
  file_path: string | null
  size_bytes: number | null
  parent_id: string | null
  season_number: number | null
  episode_number: number | null
}

export type MediaItemDetails = {
  item: MediaItem
  children: MediaItem[]
}

export type AssetRow = {
  asset_id: number
  media_item_id: string
  profile_id: string
  filename: string
  status: string
  status_detail: string | null
  size_bytes: number | null
  ready_at: string | null
  bytes_downloaded: number | null
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
