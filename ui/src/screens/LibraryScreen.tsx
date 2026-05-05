import { useMemo, useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { getClients, getLibraries, getLibraryItems, getMediaItem } from '../api'
import { useAssets } from '../api/media'
import { Badge } from '../components/Badge'
import { Btn } from '../components/Btn'
import { IcoChevR, IcoFilm, IcoSearch, IcoTV } from '../components/icons'
import { PillTabs } from '../components/PillTabs'
import { SynInput } from '../components/SynInput'
import type { MediaItem } from '../types'

type EpisodeNode = {
  id: string
  code: string
  title: string
}

type SeasonNode = {
  id: string
  title: string
  seasonNumber: number | null
  episodes: EpisodeNode[]
}

type ItemNode = {
  id: string
  title: string
  type: string
  year: number | null
  seasons: SeasonNode[]
  episodes: EpisodeNode[]
}

type LibraryNode = {
  id: string
  title: string
  type: string
  items: ItemNode[]
}

function formatCount(value: number, noun: string): string {
  return `${value} ${noun}${value === 1 ? '' : 's'}`
}

function matchesSearch(value: string, search: string): boolean {
  return value.toLowerCase().includes(search)
}

function buildEpisodeCode(item: MediaItem): string {
  if (item.type === 'movie') {
    return 'Movie'
  }

  const season = String(item.season_number ?? 0).padStart(2, '0')
  const episode = String(item.episode_number ?? 0).padStart(2, '0')
  return `S${season}E${episode}`
}

function filterLibraryTree(libraries: LibraryNode[], search: string): LibraryNode[] {
  if (!search) {
    return libraries
  }

  return libraries
    .map((library) => {
      const items = library.items
        .map((item) => {
          const matchingEpisodes = item.episodes.filter(
            (episode) =>
              matchesSearch(episode.title, search) ||
              matchesSearch(episode.code, search) ||
              matchesSearch(item.title, search) ||
              matchesSearch(library.title, search),
          )

          const matchingSeasons = item.seasons
            .map((season) => ({
              ...season,
              episodes: season.episodes.filter(
                (episode) =>
                  matchesSearch(episode.title, search) ||
                  matchesSearch(episode.code, search) ||
                  matchesSearch(season.title, search) ||
                  matchesSearch(item.title, search) ||
                  matchesSearch(library.title, search),
              ),
            }))
            .filter((season) => season.episodes.length > 0 || matchesSearch(season.title, search))

          const itemMatches = matchesSearch(item.title, search)
          if (itemMatches && item.seasons.length > 0) {
            return item
          }
          if (itemMatches && item.episodes.length > 0) {
            return item
          }

          return {
            ...item,
            episodes: matchingEpisodes,
            seasons: matchingSeasons,
          }
        })
        .filter((item) => item.episodes.length > 0 || item.seasons.length > 0 || matchesSearch(item.title, search))

      if (items.length > 0 || matchesSearch(library.title, search)) {
        return {
          ...library,
          items: items.length > 0 ? items : library.items,
        }
      }

      return null
    })
    .filter((library): library is LibraryNode => library !== null)
}

function useLibraryTree() {
  const librariesQuery = useQuery({
    queryKey: ['media', 'libraries'],
    queryFn: getLibraries,
  })

  const clientsQuery = useQuery({
    queryKey: ['clients'],
    queryFn: getClients,
  })

  const libraryItemsQueries = useQueries({
    queries: (librariesQuery.data ?? []).map((library) => ({
      queryKey: ['media', 'library', library.id],
      queryFn: () => getLibraryItems(library.id),
      enabled: librariesQuery.isSuccess,
    })),
  })

  const topLevelItems = useMemo(
    () => libraryItemsQueries.flatMap((query) => query.data ?? []),
    [libraryItemsQueries],
  )

  const topLevelDetailsQueries = useQueries({
    queries: topLevelItems.map((item) => ({
      queryKey: ['media', 'item', item.id],
      queryFn: () => getMediaItem(item.id),
      enabled: topLevelItems.length > 0,
    })),
  })

  const seasonItems = useMemo(
    () =>
      topLevelDetailsQueries.flatMap((query) =>
        (query.data?.children ?? []).filter((child) => child.type === 'season'),
      ),
    [topLevelDetailsQueries],
  )

  const seasonDetailsQueries = useQueries({
    queries: seasonItems.map((season) => ({
      queryKey: ['media', 'season', season.id],
      queryFn: () => getMediaItem(season.id),
      enabled: seasonItems.length > 0,
    })),
  })

  const libraries = useMemo<LibraryNode[]>(() => {
    const itemMap = new Map<string, MediaItem[]>()
    ;(librariesQuery.data ?? []).forEach((library, index) => {
      itemMap.set(library.id, libraryItemsQueries[index]?.data ?? [])
    })

    const detailMap = new Map(topLevelDetailsQueries.map((query) => [query.data?.item.id, query.data] as const))
    const seasonMap = new Map(seasonDetailsQueries.map((query) => [query.data?.item.id, query.data] as const))

    return (librariesQuery.data ?? []).map((library) => ({
      id: library.id,
      title: library.title,
      type: library.type,
      items: (itemMap.get(library.id) ?? []).map((item) => {
        const detail = detailMap.get(item.id)
        const seasonChildren = (detail?.children ?? []).filter((child) => child.type === 'season')
        const episodeChildren = (detail?.children ?? []).filter((child) => child.type === 'episode')

        if (seasonChildren.length > 0) {
          return {
            id: item.id,
            title: item.title,
            type: item.type,
            year: item.year,
            episodes: [],
            seasons: seasonChildren.map((season) => {
              const seasonDetail = seasonMap.get(season.id)
              return {
                id: season.id,
                title: season.title,
                seasonNumber: season.season_number,
                episodes: (seasonDetail?.children ?? []).map((episode) => ({
                  id: episode.id,
                  code: buildEpisodeCode(episode),
                  title: episode.title,
                })),
              }
            }),
          }
        }

        const sourceEpisodes = episodeChildren.length > 0 ? episodeChildren : [item]
        return {
          id: item.id,
          title: item.title,
          type: item.type,
          year: item.year,
          seasons: [],
          episodes: sourceEpisodes.map((episode) => ({
            id: episode.id,
            code: buildEpisodeCode(episode),
            title: episode.title,
          })),
        }
      }),
    }))
  }, [librariesQuery.data, libraryItemsQueries, topLevelDetailsQueries, seasonDetailsQueries])

  const isLoading =
    librariesQuery.isLoading ||
    clientsQuery.isLoading ||
    libraryItemsQueries.some((query) => query.isLoading) ||
    topLevelDetailsQueries.some((query) => query.isLoading) ||
    seasonDetailsQueries.some((query) => query.isLoading)

  const error =
    librariesQuery.error ??
    clientsQuery.error ??
    libraryItemsQueries.find((query) => query.error)?.error ??
    topLevelDetailsQueries.find((query) => query.error)?.error ??
    seasonDetailsQueries.find((query) => query.error)?.error ??
    null

  return {
    clients: clientsQuery.data ?? [],
    libraries,
    isLoading,
    error,
  }
}

function statusToBadgeColor(status: string): BadgeColor {
  if (status === 'ready') return 'ready'
  if (status === 'transcoding') return 'transcoding'
  if (status === 'queued') return 'queued'
  if (status === 'failed') return 'failed'
  return 'default'
}

export function LibraryScreen() {
  const [openMap, setOpenMap] = useState<Record<string, boolean>>({})
  const [activeFilter, setActiveFilter] = useState('all')
  const [search, setSearch] = useState('')
  const { clients, libraries, isLoading, error } = useLibraryTree()

  // Collect episode IDs from all expanded items/seasons so we can fetch their asset status.
  const expandedEpisodeIds = useMemo(() => {
    const ids: string[] = []
    for (const library of libraries) {
      for (const item of library.items) {
        const itemOpen = openMap[item.id] ?? false
        if (!itemOpen) continue
        for (const episode of item.episodes) {
          ids.push(episode.id)
        }
        for (const season of item.seasons) {
          const seasonOpen = openMap[season.id] ?? false
          if (!seasonOpen) continue
          for (const episode of season.episodes) {
            ids.push(episode.id)
          }
        }
      }
    }
    return ids
  }, [libraries, openMap])

  const assetsQuery = useAssets(expandedEpisodeIds)

  const assetMap = useMemo(() => {
    const map = new Map<string, { status: string }>()
    for (const asset of assetsQuery.data ?? []) {
      map.set(asset.media_item_id, asset)
    }
    return map
  }, [assetsQuery.data])

  const normalizedSearch = search.trim().toLowerCase()
  const filteredLibraries = useMemo(
    () => filterLibraryTree(libraries, normalizedSearch),
    [libraries, normalizedSearch],
  )

  const assetCount = useMemo(
    () =>
      libraries.reduce((libraryTotal, library) => {
        return (
          libraryTotal +
          library.items.reduce((itemTotal, item) => {
            return itemTotal + item.episodes.length + item.seasons.reduce((total, season) => total + season.episodes.length, 0)
          }, 0)
        )
      }, 0),
    [libraries],
  )

  const clearVisible = Boolean(search.trim()) || activeFilter !== 'all'

  const toggleOpen = (id: string) => {
    setOpenMap((current) => ({
      ...current,
      [id]: !current[id],
    }))
  }

  return (
    <section className="screen">
      <header className="screen-header">
        <div>
          <div className="section-label">Library</div>
          <h2 className="screen-title">Browse source media</h2>
          <p className="screen-subtitle">
            {formatCount(libraries.length, 'library')} · {formatCount(assetCount, 'asset')}
          </p>
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
              placeholder="Search libraries, titles, or episodes"
              prefixIcon={<IcoSearch />}
            />
            {clearVisible ? (
              <Btn
                size="small"
                onClick={() => {
                  setSearch('')
                  setActiveFilter('all')
                }}
              >
                Clear
              </Btn>
            ) : null}
          </div>
        </div>

        <div className="library-tree">
          {isLoading ? <div className="notice">Loading library tree…</div> : null}
          {error ? <div className="notice notice--error">{error.message}</div> : null}
          {!isLoading && !error && filteredLibraries.length === 0 ? (
            <div className="notice">No media matches the current filter.</div>
          ) : null}

          {filteredLibraries.map((library) => {
            const libraryOpen = openMap[library.id] ?? true

            return (
              <div key={library.id}>
                <TreeRow
                  depth={0}
                  expandable
                  open={libraryOpen}
                  title={library.title}
                  titleClassName="tree-row__title--library"
                  icon={library.type === 'movie' ? <IcoFilm className="tree-icon" /> : <IcoTV className="tree-icon" />}
                  meta={formatCount(library.items.length, 'item')}
                  clients={clients.map((client) => client.name)}
                  onClick={() => toggleOpen(library.id)}
                />

                {libraryOpen
                  ? library.items.map((item) => {
                      const itemOpen = openMap[item.id] ?? false
                      const itemEpisodes = item.episodes.length > 0 ? item.episodes.length : item.seasons.reduce((total, season) => total + season.episodes.length, 0)

                      return (
                        <div key={item.id}>
                          <TreeRow
                            depth={1}
                            expandable
                            open={itemOpen}
                            title={item.title}
                            titleClassName="tree-row__title--item"
                            icon={item.type === 'movie' ? <IcoFilm className="tree-icon" /> : <IcoTV className="tree-icon" />}
                            meta={[item.year ? String(item.year) : null, formatCount(itemEpisodes, 'episode')].filter(Boolean).join(' · ')}
                            clients={clients.map((client) => client.name)}
                            onClick={() => toggleOpen(item.id)}
                          />

                          {itemOpen && item.seasons.length > 0
                            ? item.seasons.map((season) => {
                                const seasonOpen = openMap[season.id] ?? false
                                return (
                                  <div key={season.id}>
                                    <TreeRow
                                      depth={2}
                                      expandable
                                      open={seasonOpen}
                                      title={season.title}
                                      titleClassName="tree-row__title--item"
                                      meta={formatCount(season.episodes.length, 'episode')}
                                      clients={clients.map((client) => client.name)}
                                      onClick={() => toggleOpen(season.id)}
                                    />

                                    {seasonOpen
                                      ? season.episodes.map((episode) => {
                                          const asset = assetMap.get(episode.id)
                                          return (
                                            <TreeRow
                                              key={episode.id}
                                              depth={3}
                                              title={`${episode.code} · ${episode.title}`}
                                              titleClassName="tree-row__title--episode mono"
                                              clients={clients.map((client) => client.name)}
                                              badgeLabel={asset ? asset.status : '–'}
                                              badgeColor={asset ? statusToBadgeColor(asset.status) : 'default'}
                                            />
                                          )
                                        })
                                      : null}
                                  </div>
                                )
                              })
                            : null}

                          {itemOpen && item.episodes.length > 0
                            ? item.episodes.map((episode) => {
                                const asset = assetMap.get(episode.id)
                                return (
                                  <TreeRow
                                    key={episode.id}
                                    depth={2}
                                    title={`${episode.code} · ${episode.title}`}
                                    titleClassName="tree-row__title--episode mono"
                                    clients={clients.map((client) => client.name)}
                                    badgeLabel={asset ? asset.status : '–'}
                                    badgeColor={asset ? statusToBadgeColor(asset.status) : 'default'}
                                  />
                                )
                              })
                            : null}
                        </div>
                      )
                    })
                  : null}
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

type BadgeColor = 'ready' | 'transcoding' | 'queued' | 'failed' | 'default'

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
  depth,
  title,
  titleClassName = '',
  meta,
  badgeLabel,
  badgeColor = 'default',
  clients,
  expandable = false,
  open = false,
  icon,
  onClick,
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
          <span className={`chevron${open ? ' chevron--open' : ''}`}>
            <IcoChevR />
          </span>
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
          <button
            key={client}
            type="button"
            className="sync-pill"
            onClick={(event) => {
              event.stopPropagation()
            }}
          >
            {client}
          </button>
        ))}
      </div>
    </div>
  )
}
