import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getClients, getLibraries, getLibraryItems, getMediaItem } from '../api'
import { getAssets } from '../api/media'
import { Badge } from '../components/Badge'
import { Btn } from '../components/Btn'
import { IcoChevR, IcoFilm, IcoSearch, IcoTV } from '../components/icons'
import { PillTabs } from '../components/PillTabs'
import { SynInput } from '../components/SynInput'
import type { MediaItem } from '../types'

type BadgeColor = 'ready' | 'transcoding' | 'queued' | 'failed' | 'default'

function formatCount(value: number, noun: string): string {
  return `${value} ${noun}${value === 1 ? '' : 's'}`
}

function buildEpisodeCode(item: MediaItem): string {
  if (item.type === 'movie') return 'Movie'
  const season = String(item.season_number ?? 0).padStart(2, '0')
  const episode = String(item.episode_number ?? 0).padStart(2, '0')
  return `S${season}E${episode}`
}

function statusToBadgeColor(status: string): BadgeColor {
  if (status === 'ready') return 'ready'
  if (status === 'transcoding') return 'transcoding'
  if (status === 'queued') return 'queued'
  if (status === 'failed') return 'failed'
  return 'default'
}

// ── Season row — fetches episodes lazily when expanded ────────────────────────

function SeasonRow({ season, clients, depth }: { season: MediaItem; clients: string[]; depth: number }) {
  const [isOpen, setIsOpen] = useState(false)

  const detailQuery = useQuery({
    queryKey: ['media', 'season', season.id],
    queryFn: () => getMediaItem(season.id),
    enabled: isOpen,
    staleTime: 60_000,
  })

  const episodes = detailQuery.data?.children ?? []

  const assetsQuery = useQuery({
    queryKey: ['assets', episodes.map((e) => e.id)],
    queryFn: () => getAssets(episodes.map((e) => e.id)),
    enabled: isOpen && episodes.length > 0,
    staleTime: 30_000,
  })

  const assetMap = useMemo(() => {
    const map = new Map<string, { status: string }>()
    for (const asset of assetsQuery.data ?? []) map.set(asset.media_item_id, asset)
    return map
  }, [assetsQuery.data])

  return (
    <>
      <TreeRow
        depth={depth}
        expandable
        open={isOpen}
        title={season.title}
        titleClassName="tree-row__title--item"
        meta={detailQuery.isLoading ? 'Loading…' : formatCount(episodes.length, 'episode')}
        clients={clients}
        onClick={() => setIsOpen((v) => !v)}
      />
      {isOpen
        ? episodes.map((episode) => {
            const asset = assetMap.get(episode.id)
            return (
              <TreeRow
                key={episode.id}
                depth={depth + 1}
                title={`${buildEpisodeCode(episode)} · ${episode.title}`}
                titleClassName="tree-row__title--episode mono"
                clients={clients}
                badgeLabel={asset ? asset.status : '–'}
                badgeColor={asset ? statusToBadgeColor(asset.status) : 'default'}
              />
            )
          })
        : null}
    </>
  )
}

// ── Show / movie row — fetches seasons lazily when expanded ───────────────────

function ShowRow({ item, clients, depth }: { item: MediaItem; clients: string[]; depth: number }) {
  const [isOpen, setIsOpen] = useState(false)

  const detailQuery = useQuery({
    queryKey: ['media', 'item', item.id],
    queryFn: () => getMediaItem(item.id),
    enabled: isOpen,
    staleTime: 60_000,
  })

  const children = detailQuery.data?.children ?? []
  const seasons = children.filter((c) => c.type === 'season')
  const episodes = children.filter((c) => c.type === 'episode')

  // For flat episode lists (mini-series without seasons)
  const assetsQuery = useQuery({
    queryKey: ['assets', episodes.map((e) => e.id)],
    queryFn: () => getAssets(episodes.map((e) => e.id)),
    enabled: isOpen && episodes.length > 0 && seasons.length === 0,
    staleTime: 30_000,
  })

  const assetMap = useMemo(() => {
    const map = new Map<string, { status: string }>()
    for (const asset of assetsQuery.data ?? []) map.set(asset.media_item_id, asset)
    return map
  }, [assetsQuery.data])

  const metaParts = [
    item.year ? String(item.year) : null,
    detailQuery.isLoading ? 'Loading…' : null,
  ].filter(Boolean)

  return (
    <>
      <TreeRow
        depth={depth}
        expandable
        open={isOpen}
        title={item.title}
        titleClassName="tree-row__title--item"
        icon={item.type === 'movie' ? <IcoFilm className="tree-icon" /> : <IcoTV className="tree-icon" />}
        meta={metaParts.join(' · ') || undefined}
        clients={clients}
        onClick={() => setIsOpen((v) => !v)}
      />
      {isOpen && seasons.length > 0
        ? seasons.map((season) => <SeasonRow key={season.id} season={season} clients={clients} depth={depth + 1} />)
        : null}
      {isOpen && seasons.length === 0
        ? episodes.map((episode) => {
            const asset = assetMap.get(episode.id)
            return (
              <TreeRow
                key={episode.id}
                depth={depth + 1}
                title={`${buildEpisodeCode(episode)} · ${episode.title}`}
                titleClassName="tree-row__title--episode mono"
                clients={clients}
                badgeLabel={asset ? asset.status : '–'}
                badgeColor={asset ? statusToBadgeColor(asset.status) : 'default'}
              />
            )
          })
        : null}
    </>
  )
}

// ── Library section — fetches items when expanded ─────────────────────────────

function LibrarySection({
  library,
  clients,
  search,
}: {
  library: { id: string; title: string; type: string }
  clients: string[]
  search: string
}) {
  const [isOpen, setIsOpen] = useState(false)

  const itemsQuery = useQuery({
    queryKey: ['media', 'library', library.id],
    queryFn: () => getLibraryItems(library.id),
    staleTime: 60_000,
  })

  const items = useMemo(() => {
    const all = itemsQuery.data ?? []
    if (!search) return all
    return all.filter((item) => item.title.toLowerCase().includes(search.toLowerCase()))
  }, [itemsQuery.data, search])

  return (
    <div>
      <TreeRow
        depth={0}
        expandable
        open={isOpen}
        title={library.title}
        titleClassName="tree-row__title--library"
        icon={library.type === 'movie' ? <IcoFilm className="tree-icon" /> : <IcoTV className="tree-icon" />}
        meta={itemsQuery.isLoading ? 'Loading…' : formatCount(items.length, 'item')}
        clients={clients.map((c) => c)}
        onClick={() => setIsOpen((v) => !v)}
      />
      {isOpen
        ? items.map((item) => <ShowRow key={item.id} item={item} clients={clients} depth={1} />)
        : null}
    </div>
  )
}

// ── Main screen ───────────────────────────────────────────────────────────────

export function LibraryScreen() {
  const [activeFilter, setActiveFilter] = useState('all')
  const [search, setSearch] = useState('')

  const librariesQuery = useQuery({
    queryKey: ['media', 'libraries'],
    queryFn: getLibraries,
    staleTime: 60_000,
  })

  const clientsQuery = useQuery({
    queryKey: ['clients'],
    queryFn: getClients,
    staleTime: 60_000,
  })

  const libraries = librariesQuery.data ?? []
  const clients = (clientsQuery.data ?? []).map((c) => c.name)
  const isLoading = librariesQuery.isLoading || clientsQuery.isLoading
  const error = librariesQuery.error ?? clientsQuery.error ?? null

  const clearVisible = Boolean(search.trim()) || activeFilter !== 'all'

  return (
    <section className="screen">
      <header className="screen-header">
        <div>
          <div className="section-label">Library</div>
          <h2 className="screen-title">Browse source media</h2>
          <p className="screen-subtitle">{formatCount(libraries.length, 'library')}</p>
        </div>
      </header>

      <div className="card">
        <div className="library-toolbar">
          <PillTabs
            tabs={[
              { label: 'All', value: 'all' },
              { label: 'Ready', value: 'ready' },
              { label: 'Transcoding', value: 'transcoding' },
              { label: 'Downloading', value: 'downloading' },
              { label: 'Queued', value: 'queued' },
              { label: 'Failed', value: 'failed' },
            ]}
            active={activeFilter}
            onChange={setActiveFilter}
          />
          <div className="inline-cluster">
            <SynInput
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search libraries or titles"
              prefixIcon={<IcoSearch />}
            />
            {clearVisible ? (
              <Btn size="small" onClick={() => { setSearch(''); setActiveFilter('all') }}>
                Clear
              </Btn>
            ) : null}
          </div>
        </div>

        <div className="library-tree">
          {isLoading ? <div className="notice">Loading libraries…</div> : null}
          {error ? <div className="notice notice--error">{(error as Error).message}</div> : null}
          {!isLoading && !error && libraries.length === 0 ? (
            <div className="notice">No libraries found.</div>
          ) : null}

          {libraries.map((library) => (
            <LibrarySection
              key={library.id}
              library={library}
              clients={clients}
              search={search}
            />
          ))}
        </div>
      </div>
    </section>
  )
}

// ── Tree row ──────────────────────────────────────────────────────────────────

type TreeRowProps = {
  depth: number
  title: string
  titleClassName?: string
  meta?: string
  badgeLabel?: string
  badgeColor?: BadgeColor
  clients: string[]
  expandable?: boolean
  open?: boolean
  icon?: React.ReactNode
  onClick?: () => void
}

function TreeRow({
  depth, title, titleClassName = '', meta, badgeLabel, badgeColor = 'default',
  clients, expandable = false, open = false, icon, onClick,
}: TreeRowProps) {
  return (
    <div
      className="tree-row"
      style={{ paddingLeft: `${16 + depth * 20}px` }}
      onClick={onClick}
      onKeyDown={(event) => {
        if (expandable && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault()
          onClick?.()
        }
      }}
      role={expandable ? 'button' : undefined}
      tabIndex={expandable ? 0 : undefined}
    >
      <div className="tree-row__left">
        {expandable ? (
          <span className={`chevron${open ? ' chevron--open' : ''}`}><IcoChevR /></span>
        ) : (
          <span className="chevron-placeholder" />
        )}
        {icon}
        <span className={`tree-row__title ${titleClassName}`.trim()}>{title}</span>
      </div>
      <div className="tree-row__right">
        {meta ? <span className="tree-meta">{meta}</span> : null}
        {badgeLabel ? <Badge color={badgeColor} label={badgeLabel} /> : null}
        {clients.map((client) => (
          <button key={client} type="button" className="sync-pill" onClick={(e) => e.stopPropagation()}>
            {client}
          </button>
        ))}
      </div>
    </div>
  )
}
