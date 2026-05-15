import type {
  AssetRow,
  Client,
  ClientAssignment,
  ClientCreateResponse,
  MediaItem,
  MediaItemDetails,
  MediaLibrary,
  PipelineStatus,
  Profile,
  QueueRow,
  Subscription,
  TransferMode,
} from './types'

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: unknown
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Authorization', `Bearer ${localStorage.getItem('ui_token') ?? ''}`)

  const hasBody = options.body !== undefined
  if (hasBody) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, {
    ...options,
    headers,
    body: hasBody ? JSON.stringify(options.body) : undefined,
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `Request failed: ${response.status}`)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export async function getClients(): Promise<Client[]> {
  const payload = await request<{ clients: Client[] }>('/api/clients')
  return payload.clients
}

export async function createClient(input: {
  id: string
  name: string
  storage_budget_bytes: number | null
}): Promise<ClientCreateResponse> {
  return request<ClientCreateResponse>('/api/clients', {
    method: 'POST',
    body: input,
  })
}

export async function deleteClient(clientId: string): Promise<void> {
  await request<void>(`/api/clients/${clientId}`, { method: 'DELETE' })
}

export async function getProfiles(): Promise<Profile[]> {
  const payload = await request<{ profiles: Profile[] }>('/api/profiles')
  return payload.profiles
}

export async function createProfile(input: {
  id: string
  name: string
  ffmpeg_args: string[] | null
  target_size_bytes: number | null
}): Promise<Profile> {
  return request<Profile>('/api/profiles', {
    method: 'POST',
    body: input,
  })
}

export async function deleteProfile(profileId: string): Promise<void> {
  await request<void>(`/api/profiles/${profileId}`, { method: 'DELETE' })
}

export async function getSubscriptions(clientId?: string): Promise<Subscription[]> {
  const searchParams = new URLSearchParams()
  if (clientId) {
    searchParams.set('client_id', clientId)
  }

  const query = searchParams.toString()
  const payload = await request<{ subscriptions: Subscription[] }>(
    `/api/subscriptions${query ? `?${query}` : ''}`,
  )
  return payload.subscriptions
}

export async function createSubscription(input: {
  client_id: string
  media_item_id: string
  scope_type: string
  scope_params: Record<string, unknown> | null
  profile_id: string
}): Promise<Subscription> {
  return request<Subscription>('/api/subscriptions', {
    method: 'POST',
    body: input,
  })
}

export async function deleteSubscription(subscriptionId: number): Promise<void> {
  await request<void>(`/api/subscriptions/${subscriptionId}`, { method: 'DELETE' })
}

export async function getLibraries(): Promise<MediaLibrary[]> {
  const payload = await request<{ libraries: MediaLibrary[] }>('/api/media/libraries')
  return payload.libraries
}

export async function getLibraryItems(libraryId: string): Promise<MediaItem[]> {
  const payload = await request<{ items: MediaItem[] }>(`/api/media/library/${libraryId}/items`)
  return payload.items
}

export async function getMediaItem(itemId: string): Promise<MediaItemDetails> {
  return request<MediaItemDetails>(`/api/media/item/${itemId}`)
}

export async function getAllAssets(): Promise<AssetRow[]> {
  return request<AssetRow[]>('/api/assets')
}

export async function deleteAsset(assetId: number): Promise<void> {
  await request<void>(`/api/assets/${assetId}`, { method: 'DELETE' })
}

export async function getClientAssignments(
  clientId: string,
  mediaItemIds: string[],
): Promise<ClientAssignment[]> {
  if (mediaItemIds.length === 0) return []

  const searchParams = new URLSearchParams({
    media_item_ids: mediaItemIds.join(','),
  })
  return request<ClientAssignment[]>(
    `/api/clients/${encodeURIComponent(clientId)}/assignments?${searchParams.toString()}`,
  )
}

export async function getQueue(params?: {
  status?: PipelineStatus[]
  client_id?: string
}): Promise<{ rows: QueueRow[] }> {
  const search = new URLSearchParams()
  if (params?.status) {
    for (const s of params.status) search.append('status', s)
  }
  if (params?.client_id) search.set('client_id', params.client_id)
  const qs = search.toString()
  return request<{ rows: QueueRow[] }>(`/api/queue${qs ? `?${qs}` : ''}`)
}

export async function createSubscriptionsBatch(
  items: Array<{
    client_id: string
    media_item_id: string
    scope_type: string
    scope_params: Record<string, unknown> | null
    profile_id: string
  }>,
): Promise<void> {
  await request<unknown>('/api/subscriptions/batch', {
    method: 'POST',
    body: { subscriptions: items },
  })
}

export async function retryQueueItem(clientId: string, assetId: number): Promise<void> {
  return request<void>(`/api/queue/${clientId}/${assetId}/retry`, { method: 'POST' })
}

export async function getTransferMode(): Promise<{ transfer_mode: TransferMode }> {
  return request<{ transfer_mode: TransferMode }>('/api/transfer-mode')
}

export async function setTransferMode(
  mode: TransferMode,
): Promise<{ transfer_mode: TransferMode }> {
  return request<{ transfer_mode: TransferMode }>('/api/transfer-mode', {
    method: 'PUT',
    body: { transfer_mode: mode },
  })
}
