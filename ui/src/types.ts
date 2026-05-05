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
