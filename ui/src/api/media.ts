import type { UseQueryResult } from '@tanstack/react-query'
import { useQuery } from '@tanstack/react-query'

export type AssetStatus = {
  media_item_id: string
  profile_id: string
  status: string
  size_bytes: number | null
  ready_at: string | null
}

async function apiFetch<T>(path: string): Promise<T> {
  const headers = new Headers()
  headers.set('Authorization', `Bearer ${localStorage.getItem('ui_token') ?? ''}`)

  const response = await fetch(`/api${path}`, { headers })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `Request failed: ${response.status}`)
  }

  return (await response.json()) as T
}

export async function getAssets(mediaItemIds: string[]): Promise<AssetStatus[]> {
  if (mediaItemIds.length === 0) return []
  const headers = new Headers()
  headers.set('Authorization', `Bearer ${localStorage.getItem('ui_token') ?? ''}`)
  const response = await fetch(`/api/assets?media_item_ids=${mediaItemIds.join(',')}`, { headers })
  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `Request failed: ${response.status}`)
  }
  return (await response.json()) as AssetStatus[]
}

export function useAssets(mediaItemIds: string[]): UseQueryResult<AssetStatus[]> {
  return useQuery({
    queryKey: ['assets', mediaItemIds],
    queryFn: () => apiFetch<AssetStatus[]>(`/assets?media_item_ids=${mediaItemIds.join(',')}`),
    enabled: mediaItemIds.length > 0,
    staleTime: 30_000,
  })
}
