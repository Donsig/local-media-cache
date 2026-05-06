import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteAsset, getAllAssets } from '../api'
import { Badge } from '../components/Badge'
import { Btn } from '../components/Btn'
import { PillTabs } from '../components/PillTabs'
import type { AssetRow } from '../types'

type BadgeColor = 'ready' | 'transcoding' | 'queued' | 'failed' | 'default'

function statusToBadgeColor(status: string): BadgeColor {
  if (status === 'ready') return 'ready'
  if (status === 'transcoding') return 'transcoding'
  if (status === 'queued') return 'queued'
  if (status === 'failed') return 'failed'
  return 'default'
}

const STATUS_SORT_ORDER: Record<string, number> = {
  transcoding: 0,
  queued: 1,
  failed: 2,
  ready: 3,
}

function sortOrder(status: string): number {
  return STATUS_SORT_ORDER[status] ?? 99
}

function formatBytes(bytes: number | null): string {
  if (bytes === null) return '–'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function formatRelativeTime(isoString: string): string {
  const date = new Date(isoString)
  const diffMs = Date.now() - date.getTime()
  const diffMins = Math.floor(diffMs / 60_000)
  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  return `${Math.floor(diffHours / 24)}d ago`
}

const ACTIVE_STATUSES = new Set(['transcoding', 'downloading', 'queued'])

function AssetRowItem({ asset, onDelete }: { asset: AssetRow; onDelete: () => void }) {
  const isActive = ACTIVE_STATUSES.has(asset.status)

  return (
    <div className="queue-row">
      <div className="queue-row__status">
        <Badge color={statusToBadgeColor(asset.status)} label={asset.status} />
      </div>
      <div className="queue-row__main">
        <span className="queue-row__filename">{asset.filename}</span>
        {isActive ? (
          <div className="queue-row__progress">
            <div className="progress__track" aria-hidden="true">
              <div className="progress__fill progress__fill--indeterminate" />
            </div>
          </div>
        ) : null}
        {asset.status === 'failed' && asset.status_detail ? (
          <span className="queue-row__detail">{asset.status_detail}</span>
        ) : null}
      </div>
      <div className="queue-row__meta">
        <span className="queue-row__profile">{asset.profile_id}</span>
        <span className="queue-row__size">{formatBytes(asset.size_bytes)}</span>
        {asset.ready_at ? (
          <span className="queue-row__ready">ready {formatRelativeTime(asset.ready_at)}</span>
        ) : null}
        <Btn size="small" variant="danger" onClick={onDelete}>Remove</Btn>
      </div>
    </div>
  )
}

const FILTER_TABS = [
  { label: 'All', value: 'all' },
  { label: 'Queued', value: 'queued' },
  { label: 'Transcoding', value: 'transcoding' },
  { label: 'Ready', value: 'ready' },
  { label: 'Failed', value: 'failed' },
]

export function QueueScreen() {
  const [activeFilter, setActiveFilter] = useState('all')
  const queryClient = useQueryClient()

  const assetsQuery = useQuery({
    queryKey: ['assets', 'all'],
    queryFn: getAllAssets,
    staleTime: 10_000,
    refetchInterval: 15_000,
  })

  const deleteMutation = useMutation({
    mutationFn: deleteAsset,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assets'] }),
  })

  const assets = assetsQuery.data ?? []

  const counts = useMemo(() => {
    const queued = assets.filter((a) => a.status === 'queued').length
    const transcoding = assets.filter((a) => a.status === 'transcoding').length
    const ready = assets.filter((a) => a.status === 'ready').length
    return { queued, transcoding, ready }
  }, [assets])

  const subtitle = [
    counts.queued > 0 ? `${counts.queued} queued` : null,
    counts.transcoding > 0 ? `${counts.transcoding} transcoding` : null,
    counts.ready > 0 ? `${counts.ready} ready` : null,
  ]
    .filter(Boolean)
    .join(' · ') || 'No active transfers'

  const filtered = useMemo(() => {
    const list = activeFilter === 'all'
      ? [...assets]
      : assets.filter((a) => a.status === activeFilter)
    return list.sort((a, b) => sortOrder(a.status) - sortOrder(b.status))
  }, [assets, activeFilter])

  return (
    <section className="screen">
      <header className="screen-header">
        <div>
          <div className="section-label">Queue</div>
          <h2 className="screen-title">Transfer Status</h2>
          <p className="screen-subtitle">{subtitle}</p>
        </div>
      </header>

      <div className="card">
        <div className="library-toolbar">
          <PillTabs tabs={FILTER_TABS} active={activeFilter} onChange={setActiveFilter} />
        </div>

        {assetsQuery.isLoading ? (
          <div className="notice">Loading…</div>
        ) : assetsQuery.error ? (
          <div className="notice notice--error">{(assetsQuery.error as Error).message}</div>
        ) : filtered.length === 0 ? (
          <div className="notice">
            {activeFilter === 'all'
              ? 'No assets yet. Subscribe to content in the Library.'
              : `No ${activeFilter} assets.`}
          </div>
        ) : (
          <div className="queue-list">
            {filtered.map((asset) => (
              <AssetRowItem
                key={asset.asset_id}
                asset={asset}
                onDelete={() => deleteMutation.mutate(asset.asset_id)}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
