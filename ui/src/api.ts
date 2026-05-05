import type { Client, ClientCreateResponse, MediaItem, MediaItemDetails, MediaLibrary, Profile } from './types'

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
