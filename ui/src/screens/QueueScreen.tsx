import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getQueue } from '../api'
import { Badge } from '../components/Badge'
import { PillTabs } from '../components/PillTabs'
import type { PipelineStatus, QueueRow } from '../types'

type BadgeColor = 'ready' | 'transcoding' | 'queued' | 'failed' | 'default'

function pipelineStatusToBadgeColor(status: PipelineStatus): BadgeColor {
  if (status === 'ready') return 'ready'
  if (status === 'transferring') return 'transcoding'
  if (status === 'queued') return 'queued'
  if (status === 'failed') return 'failed'
  return 'default'
}

const PIPELINE_SORT_ORDER: Record<PipelineStatus, number> = {
  transferring: 0,
  queued: 1,
  failed: 2,
  ready: 3,
}

function formatBytes(bytes: number | null): string {
  if (bytes === null) return '–'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function formatRate(bps: number): string {
  if (bps < 1024) return `${bps.toFixed(0)} B/s`
  if (bps < 1024 ** 2) return `${(bps / 1024).toFixed(1)} KB/s`
  return `${(bps / 1024 ** 2).toFixed(1)} MB/s`
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  if (mins < 60) return `${mins}m ${secs}s`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}

function parseShowGroup(filename: string): string {
  const tvMatch = filename.match(/^(.+?)\s+-\s+S\d{2}E\d{2}/i)
  if (tvMatch) return tvMatch[1]
  const dashIdx = filename.indexOf(' - ')
  return dashIdx > 0 ? filename.slice(0, dashIdx) : filename
}

function QueueRowItem({ row }: { row: QueueRow }) {
  const isTransferring = row.pipeline_status === 'transferring'
  const isVerifying = row.pipeline_substate === 'verifying'
  const isStalled = row.pipeline_substate === 'stalled'

  const hasDeterminateProgress =
    isTransferring &&
    !isVerifying &&
    row.bytes_downloaded != null &&
    row.size_bytes != null &&
    row.size_bytes > 0 &&
    row.bytes_downloaded < row.size_bytes

  const showRateEta =
    isTransferring &&
    !isVerifying &&
    !isStalled &&
    row.transfer_rate_bps != null &&
    row.eta_seconds != null

  return (
    <div className="queue-row">
      <div className="queue-row__status">
        <Badge
          color={pipelineStatusToBadgeColor(row.pipeline_status)}
          label={row.pipeline_status === 'transferring' ? 'syncing' : row.pipeline_status}
        />
        <span className="queue-row__client" style={{ fontSize: '0.75rem', color: 'var(--color-text-muted, #888)' }}>
          to {row.client_id}
        </span>
      </div>
      <div className="queue-row__main">
        <span className="queue-row__filename">{row.filename}</span>
        {isTransferring ? (
          <div className="queue-row__progress">
            <div className="progress__track" aria-hidden="true">
              {isVerifying ? (
                <div className="progress__fill progress__fill--indeterminate" />
              ) : hasDeterminateProgress ? (
                <div
                  className="progress__fill"
                  style={{ width: `${Math.min(100, (row.bytes_downloaded! / row.size_bytes!) * 100).toFixed(1)}%` }}
                />
              ) : (
                <div className="progress__fill progress__fill--indeterminate" />
              )}
            </div>
            {hasDeterminateProgress ? (
              <span className="progress__label" style={{ fontSize: '0.75rem', color: 'var(--color-text-muted, #888)', marginTop: '2px' }}>
                {formatBytes(row.bytes_downloaded)} / {formatBytes(row.size_bytes)}
                {showRateEta ? (
                  <span style={{ marginLeft: '0.5rem' }}>
                    · {formatRate(row.transfer_rate_bps!)} · ETA {formatEta(row.eta_seconds!)}
                  </span>
                ) : null}
              </span>
            ) : null}
          </div>
        ) : null}
        {row.pipeline_detail ? (
          <span className="queue-row__detail" style={{ fontSize: '0.75rem', color: 'var(--color-text-muted, #888)', marginTop: '2px', display: 'block' }}>
            {row.pipeline_detail}
          </span>
        ) : null}
      </div>
      <div className="queue-row__meta">
        <span className="queue-row__profile">{row.profile_id}</span>
        <span className="queue-row__size">{formatBytes(row.size_bytes)}</span>
      </div>
    </div>
  )
}

const FILTER_TABS = [
  { label: 'All', value: 'all' },
  { label: 'Queued', value: 'queued' },
  { label: 'Transferring', value: 'transferring' },
  { label: 'Ready', value: 'ready' },
  { label: 'Failed', value: 'failed' },
]

export function QueueScreen() {
  const [activeFilter, setActiveFilter] = useState('all')

  const queueQuery = useQuery({
    queryKey: ['queue'],
    queryFn: () => getQueue(),
    staleTime: 10_000,
    refetchInterval: 15_000,
  })

  const rows = queueQuery.data?.rows ?? []

  const counts = useMemo(() => ({
    transferring: rows.filter((r) => r.pipeline_status === 'transferring').length,
    queued: rows.filter((r) => r.pipeline_status === 'queued').length,
    ready: rows.filter((r) => r.pipeline_status === 'ready').length,
  }), [rows])

  const subtitle = [
    counts.transferring > 0 ? `${counts.transferring} transferring` : null,
    counts.queued > 0 ? `${counts.queued} queued` : null,
    counts.ready > 0 ? `${counts.ready} ready` : null,
  ].filter(Boolean).join(' · ') || 'No active transfers'

  const filtered = useMemo(() => {
    const list = activeFilter === 'all' ? [...rows] : rows.filter((r) => r.pipeline_status === activeFilter)
    return list.sort((a, b) => (PIPELINE_SORT_ORDER[a.pipeline_status] ?? 99) - (PIPELINE_SORT_ORDER[b.pipeline_status] ?? 99))
  }, [rows, activeFilter])

  const grouped = useMemo(() => {
    const map = new Map<string, QueueRow[]>()
    for (const row of filtered) {
      const key = parseShowGroup(row.filename)
      const group = map.get(key) ?? []
      group.push(row)
      map.set(key, group)
    }
    return map
  }, [filtered])

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
        {queueQuery.isLoading ? (
          <div className="notice">Loading…</div>
        ) : queueQuery.error ? (
          <div className="notice notice--error">{(queueQuery.error as Error).message}</div>
        ) : filtered.length === 0 ? (
          <div className="notice">
            {activeFilter === 'all' ? 'No assets yet. Subscribe to content in the Library.' : `No ${activeFilter} transfers.`}
          </div>
        ) : (
          <div className="queue-list">
            {Array.from(grouped.entries()).map(([groupName, groupRows]) => (
              <div key={groupName} className="queue-group">
                <div className="queue-group__header">{groupName}</div>
                {groupRows.map((row) => (
                  <QueueRowItem key={`${row.asset_id}-${row.client_id}`} row={row} />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
