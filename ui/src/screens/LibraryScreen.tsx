import { useEffect, useMemo, useRef, useState } from 'react'
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  createSubscription,
  deleteSubscription,
  getClientAssignments,
  getClients,
  getLibraries,
  getLibraryItems,
  getMediaItem,
  getProfiles,
  getSubscriptions,
} from '../api'
import { getAssets } from '../api/media'
import { Badge } from '../components/Badge'
import { Btn } from '../components/Btn'
import { IcoChevR, IcoFilm, IcoSearch, IcoTV } from '../components/icons'
import { SynInput } from '../components/SynInput'
import type { Client, ClientAssignment, MediaItem, PipelineStatus, Profile } from '../types'

type BadgeColor = 'ready' | 'transcoding' | 'queued' | 'failed' | 'default'
type ClientAssignmentMap = Map<string, Map<string, ClientAssignment>>

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

function episodePillClassName(status: PipelineStatus | null): string {
  if (status === 'ready') return 'sync-pill sync-pill--on'
  if (status === 'transferring' || status === 'queued') return 'sync-pill sync-pill--pending'
  if (status === 'failed') return 'sync-pill sync-pill--pending'
  return 'sync-pill'
}

function assignmentFor(
  assignmentMap: ClientAssignmentMap,
  clientId: string,
  mediaItemId: string,
): ClientAssignment | null {
  return assignmentMap.get(clientId)?.get(mediaItemId) ?? null
}

function useClientAssignmentMap(
  clients: Client[],
  mediaItemIds: string[],
  enabled: boolean,
): ClientAssignmentMap {
  const queries = useQueries({
    queries: clients.map((client) => ({
      queryKey: ['clientAssignments', client.id, mediaItemIds],
      queryFn: () => getClientAssignments(client.id, mediaItemIds),
      enabled: enabled && mediaItemIds.length > 0,
      staleTime: 30_000,
    })),
  })

  return useMemo(() => {
    const map: ClientAssignmentMap = new Map()
    for (const [index, client] of clients.entries()) {
      const clientMap = new Map<string, ClientAssignment>()
      for (const assignment of queries[index]?.data ?? []) {
        clientMap.set(assignment.media_item_id, assignment)
      }
      map.set(client.id, clientMap)
    }
    return map
  }, [clients, queries])
}

function StaticClientPills({ clients }: { clients: Client[] }) {
  return (
    <>
      {clients.map((client) => (
        <span key={client.id} className="sync-pill">
          {client.name}
        </span>
      ))}
    </>
  )
}


function EpisodeSyncPill({
  client,
  mediaItemId,
  currentAssignment,
  profiles,
}: {
  client: Client
  mediaItemId: string
  currentAssignment: ClientAssignment | null
  profiles: Profile[]
}) {
  const queryClient = useQueryClient()
  const anchorRef = useRef<HTMLDivElement | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [selectedProfileId, setSelectedProfileId] = useState(profiles[0]?.id ?? '')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    if (!selectedProfileId && profiles[0]?.id) {
      setSelectedProfileId(profiles[0].id)
    }
  }, [profiles, selectedProfileId])

  const closePopover = () => {
    setPickerOpen(false)
    setErrorMessage(null)
  }

  const invalidateAssignments = () =>
    queryClient.invalidateQueries({ queryKey: ['clientAssignments', client.id] })

  const createMutation = useMutation({
    mutationFn: (profileId: string) =>
      createSubscription({
        client_id: client.id,
        media_item_id: mediaItemId,
        scope_type: 'episode',
        scope_params: null,
        profile_id: profileId,
      }),
    onSuccess: () => {
      closePopover()
      void invalidateAssignments()
    },
    onError: (error: Error) => {
      setErrorMessage(error.message)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async () => {
      const subscriptions = await getSubscriptions(client.id)
      const subscription = subscriptions.find((item) => item.media_item_id === mediaItemId)
      if (!subscription) {
        throw new Error(`Subscription for ${client.name} and ${mediaItemId} not found`)
      }
      await deleteSubscription(subscription.id)
    },
    onSuccess: () => {
      setErrorMessage(null)
      void invalidateAssignments()
    },
    onError: (error: Error) => {
      setErrorMessage(error.message)
    },
  })

  const showForm = pickerOpen && currentAssignment === null
  const showPopover = showForm || errorMessage !== null
  const isBusy = createMutation.isPending || deleteMutation.isPending

  useEffect(() => {
    if (!showPopover) return

    const handlePointerDown = (event: PointerEvent) => {
      if (
        anchorRef.current !== null &&
        event.target instanceof Node &&
        !anchorRef.current.contains(event.target)
      ) {
        closePopover()
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [showPopover])

  return (
    <div ref={anchorRef} className="sync-pill-anchor">
      <button
        type="button"
        className={episodePillClassName(currentAssignment?.pipeline_status ?? null)}
        disabled={currentAssignment?.state === 'evict' || isBusy}
        onClick={(event) => {
          event.stopPropagation()
          setErrorMessage(null)

          if (currentAssignment?.state === 'evict') {
            return
          }

          if (currentAssignment === null) {
            setPickerOpen((value) => !value)
            return
          }

          deleteMutation.mutate()
        }}
      >
        {client.name}
      </button>

      {showPopover ? (
        <div className="sync-pill-picker" onClick={(event) => event.stopPropagation()}>
          {showForm ? (
            <>
              <div className="form-field">
                <label htmlFor={`profile-${client.id}-${mediaItemId}`}>Profile</label>
                <select
                  id={`profile-${client.id}-${mediaItemId}`}
                  className="surface-input"
                  value={selectedProfileId}
                  onChange={(event) => setSelectedProfileId(event.target.value)}
                >
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </div>
              {profiles.length === 0 ? (
                <div className="notice notice--error sync-pill-picker__error">
                  No profiles available.
                </div>
              ) : null}
            </>
          ) : null}

          {errorMessage ? (
            <div className="notice notice--error sync-pill-picker__error">{errorMessage}</div>
          ) : null}

          <div className="sync-pill-picker__actions">
            {showForm ? (
              <Btn
                size="small"
                variant="primary"
                disabled={createMutation.isPending || !selectedProfileId || profiles.length === 0}
                onClick={() => createMutation.mutate(selectedProfileId)}
              >
                Subscribe
              </Btn>
            ) : null}
            <Btn size="small" onClick={closePopover}>
              {showForm ? 'Cancel' : 'Close'}
            </Btn>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function EpisodeSyncPills({
  clients,
  mediaItemId,
  profiles,
  assignmentMap,
}: {
  clients: Client[]
  mediaItemId: string
  profiles: Profile[]
  assignmentMap: ClientAssignmentMap
}) {
  return (
    <>
      {clients.map((client) => (
        <EpisodeSyncPill
          key={client.id}
          client={client}
          mediaItemId={mediaItemId}
          currentAssignment={assignmentFor(assignmentMap, client.id, mediaItemId)}
          profiles={profiles}
        />
      ))}
    </>
  )
}

// ── Bulk sync pill — season or show level subscriptions ──────────────────────

function BulkSyncPill({
  client,
  showId,
  scopeType,
  scopeParams,
  profiles,
  childMediaItemIds,
  assignmentMap,
}: {
  client: Client
  showId: string
  scopeType: 'show:all' | 'show:seasons'
  scopeParams: Record<string, unknown> | null
  profiles: Profile[]
  childMediaItemIds: string[]
  assignmentMap: ClientAssignmentMap
}) {
  const queryClient = useQueryClient()
  const anchorRef = useRef<HTMLDivElement | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [selectedProfileId, setSelectedProfileId] = useState(profiles[0]?.id ?? '')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    if (!selectedProfileId && profiles[0]?.id) {
      setSelectedProfileId(profiles[0].id)
    }
  }, [profiles, selectedProfileId])

  const subsQuery = useQuery({
    queryKey: ['subscriptions', client.id],
    queryFn: () => getSubscriptions(client.id),
    staleTime: 30_000,
  })

  const matchingSub = useMemo(() => {
    return subsQuery.data?.find((sub) => {
      if (sub.media_item_id !== showId) return false
      if (scopeType === 'show:seasons' && sub.scope_type === 'show:all') return true
      if (sub.scope_type !== scopeType) return false
      if (scopeType === 'show:seasons') {
        const subSeasons = sub.scope_params?.seasons as number[] | undefined
        const wantedSeasons = (scopeParams?.seasons ?? []) as number[]
        return wantedSeasons.every((s) => subSeasons?.includes(s))
      }
      return true
    })
  }, [subsQuery.data, showId, scopeType, scopeParams])

  const isCoveredByShowAll =
    scopeType === 'show:seasons' &&
    Boolean(matchingSub) &&
    matchingSub?.scope_type === 'show:all'
  const isSubscribed = Boolean(matchingSub)
  const bulkPipelineStatus = useMemo((): PipelineStatus | null => {
    if (!isSubscribed) return null
    const statuses = childMediaItemIds
      .map((id) => assignmentMap.get(client.id)?.get(id)?.pipeline_status)
      .filter((s): s is PipelineStatus => s != null)
    if (statuses.length === 0) return 'queued'
    if (statuses.includes('failed')) return 'failed'
    if (statuses.includes('transferring')) return 'transferring'
    if (statuses.includes('queued')) return 'queued'
    return 'ready'
  }, [childMediaItemIds, assignmentMap, client.id, isSubscribed])

  const closePopover = () => {
    setPickerOpen(false)
    setErrorMessage(null)
  }

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['subscriptions', client.id] })
    void queryClient.invalidateQueries({ queryKey: ['clientAssignments', client.id] })
  }

  const createMutation = useMutation({
    mutationFn: (profileId: string) =>
      createSubscription({
        client_id: client.id,
        media_item_id: showId,
        scope_type: scopeType,
        scope_params: scopeParams,
        profile_id: profileId,
      }),
    onSuccess: () => {
      closePopover()
      invalidate()
    },
    onError: (error: Error) => {
      setErrorMessage(error.message)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async () => {
      if (!matchingSub) throw new Error('Subscription not found')
      await deleteSubscription(matchingSub.id)
    },
    onSuccess: () => {
      setErrorMessage(null)
      invalidate()
    },
    onError: (error: Error) => {
      setErrorMessage(error.message)
    },
  })

  const showForm = pickerOpen && !isSubscribed
  const showPopover = showForm || errorMessage !== null
  const isBusy = createMutation.isPending || deleteMutation.isPending || subsQuery.isLoading

  useEffect(() => {
    if (!showPopover) return
    const handlePointerDown = (event: PointerEvent) => {
      if (
        anchorRef.current !== null &&
        event.target instanceof Node &&
        !anchorRef.current.contains(event.target)
      ) {
        closePopover()
      }
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [showPopover])

  return (
    <div ref={anchorRef} className="sync-pill-anchor">
      <button
        type="button"
        className={episodePillClassName(bulkPipelineStatus)}
        disabled={isBusy || isCoveredByShowAll}
        onClick={(event) => {
          event.stopPropagation()
          setErrorMessage(null)
          if (isSubscribed) {
            deleteMutation.mutate()
          } else {
            setPickerOpen((v) => !v)
          }
        }}
      >
        {client.name}
      </button>

      {showPopover ? (
        <div className="sync-pill-picker" onClick={(event) => event.stopPropagation()}>
          {showForm ? (
            <>
              <div className="form-field">
                <label htmlFor={`profile-bulk-${client.id}-${showId}-${scopeType}`}>Profile</label>
                <select
                  id={`profile-bulk-${client.id}-${showId}-${scopeType}`}
                  className="surface-input"
                  value={selectedProfileId}
                  onChange={(event) => setSelectedProfileId(event.target.value)}
                >
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </div>
              {profiles.length === 0 ? (
                <div className="notice notice--error sync-pill-picker__error">
                  No profiles available.
                </div>
              ) : null}
            </>
          ) : null}

          {errorMessage ? (
            <div className="notice notice--error sync-pill-picker__error">{errorMessage}</div>
          ) : null}

          <div className="sync-pill-picker__actions">
            {showForm ? (
              <Btn
                size="small"
                variant="primary"
                disabled={createMutation.isPending || !selectedProfileId || profiles.length === 0}
                onClick={() => createMutation.mutate(selectedProfileId)}
              >
                Subscribe
              </Btn>
            ) : null}
            <Btn size="small" onClick={closePopover}>
              {showForm ? 'Cancel' : 'Close'}
            </Btn>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function BulkSyncPills({
  clients,
  showId,
  scopeType,
  scopeParams,
  profiles,
  childMediaItemIds,
  assignmentMap,
}: {
  clients: Client[]
  showId: string
  scopeType: 'show:all' | 'show:seasons'
  scopeParams: Record<string, unknown> | null
  profiles: Profile[]
  childMediaItemIds: string[]
  assignmentMap: ClientAssignmentMap
}) {
  return (
    <>
      {clients.map((client) => (
        <BulkSyncPill
          key={client.id}
          client={client}
          showId={showId}
          scopeType={scopeType}
          scopeParams={scopeParams}
          profiles={profiles}
          childMediaItemIds={childMediaItemIds}
          assignmentMap={assignmentMap}
        />
      ))}
    </>
  )
}

function EpisodeRows({
  episodes,
  clients,
  profiles,
  depth,
  assetMap,
  assignmentMap,
}: {
  episodes: MediaItem[]
  clients: Client[]
  profiles: Profile[]
  depth: number
  assetMap: Map<string, { status: string }>
  assignmentMap: ClientAssignmentMap
}) {
  return (
    <>
      {episodes.map((episode) => {
        const asset = assetMap.get(episode.id)
        return (
          <TreeRow
            key={episode.id}
            depth={depth}
            title={`${buildEpisodeCode(episode)} · ${episode.title}`}
            titleClassName="tree-row__title--episode mono"
            badgeLabel={asset ? asset.status : '–'}
            badgeColor={asset ? statusToBadgeColor(asset.status) : 'default'}
            pills={(
              <EpisodeSyncPills
                clients={clients}
                mediaItemId={episode.id}
                profiles={profiles}
                assignmentMap={assignmentMap}
              />
            )}
          />
        )
      })}
    </>
  )
}

// ── Season row — fetches episodes lazily when expanded ────────────────────────

function SeasonRow({
  season,
  showId,
  clients,
  profiles,
  depth,
}: {
  season: MediaItem
  showId: string
  clients: Client[]
  profiles: Profile[]
  depth: number
}) {
  const [isOpen, setIsOpen] = useState(false)

  const detailQuery = useQuery({
    queryKey: ['media', 'season', season.id],
    queryFn: () => getMediaItem(season.id),
    enabled: isOpen,
    staleTime: 60_000,
  })

  const episodes = detailQuery.data?.children ?? []
  const episodeIds = useMemo(() => episodes.map((episode) => episode.id), [episodes])

  const assetsQuery = useQuery({
    queryKey: ['assets', episodeIds],
    queryFn: () => getAssets(episodeIds),
    enabled: isOpen && episodeIds.length > 0,
    staleTime: 30_000,
  })

  const assetMap = useMemo(() => {
    const map = new Map<string, { status: string }>()
    for (const asset of assetsQuery.data ?? []) map.set(asset.media_item_id, asset)
    return map
  }, [assetsQuery.data])

  const assignmentMap = useClientAssignmentMap(
    clients,
    episodeIds,
    isOpen && episodeIds.length > 0,
  )

  return (
    <>
      <TreeRow
        depth={depth}
        expandable
        open={isOpen}
        title={season.title}
        titleClassName="tree-row__title--item"
        meta={detailQuery.isLoading ? 'Loading…' : formatCount(episodes.length, 'episode')}
        pills={season.season_number != null ? (
          <BulkSyncPills
            clients={clients}
            showId={showId}
            scopeType="show:seasons"
            scopeParams={{ seasons: [season.season_number] }}
            profiles={profiles}
            childMediaItemIds={episodeIds}
            assignmentMap={assignmentMap}
          />
        ) : null}
        onClick={() => setIsOpen((value) => !value)}
      />
      {isOpen ? (
        <EpisodeRows
          episodes={episodes}
          clients={clients}
          profiles={profiles}
          depth={depth + 1}
          assetMap={assetMap}
          assignmentMap={assignmentMap}
        />
      ) : null}
    </>
  )
}

// ── Movie row — non-expandable leaf with interactive sync pills ───────────────

function MovieRow({
  item,
  clients,
  profiles,
  depth,
}: {
  item: MediaItem
  clients: Client[]
  profiles: Profile[]
  depth: number
}) {
  const assignmentMap = useClientAssignmentMap(clients, [item.id], true)

  const assetsQuery = useQuery({
    queryKey: ['assets', [item.id]],
    queryFn: () => getAssets([item.id]),
    staleTime: 30_000,
  })

  const asset = assetsQuery.data?.[0]

  return (
    <TreeRow
      depth={depth}
      title={item.title}
      titleClassName="tree-row__title--item"
      icon={<IcoFilm className="tree-icon" />}
      meta={item.year ? String(item.year) : undefined}
      badgeLabel={asset ? asset.status : undefined}
      badgeColor={asset ? statusToBadgeColor(asset.status) : 'default'}
      pills={(
        <EpisodeSyncPills
          clients={clients}
          mediaItemId={item.id}
          profiles={profiles}
          assignmentMap={assignmentMap}
        />
      )}
    />
  )
}

// ── Show row — fetches seasons lazily when expanded ───────────────────────────

function ShowRow({
  item,
  clients,
  profiles,
  depth,
}: {
  item: MediaItem
  clients: Client[]
  profiles: Profile[]
  depth: number
}) {
  const [isOpen, setIsOpen] = useState(false)

  const detailQuery = useQuery({
    queryKey: ['media', 'item', item.id],
    queryFn: () => getMediaItem(item.id),
    enabled: isOpen,
    staleTime: 60_000,
  })

  const children = detailQuery.data?.children ?? []
  const seasons = children.filter((child) => child.type === 'season')
  const episodes = children.filter((child) => child.type === 'episode')
  const episodeIds = useMemo(() => episodes.map((episode) => episode.id), [episodes])

  const assetsQuery = useQuery({
    queryKey: ['assets', episodeIds],
    queryFn: () => getAssets(episodeIds),
    enabled: isOpen && episodeIds.length > 0 && seasons.length === 0,
    staleTime: 30_000,
  })

  const assetMap = useMemo(() => {
    const map = new Map<string, { status: string }>()
    for (const asset of assetsQuery.data ?? []) map.set(asset.media_item_id, asset)
    return map
  }, [assetsQuery.data])

  const assignmentMap = useClientAssignmentMap(
    clients,
    episodeIds,
    isOpen && episodeIds.length > 0 && seasons.length === 0,
  )

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
        icon={<IcoTV className="tree-icon" />}
        meta={metaParts.join(' · ') || undefined}
        pills={(
          <BulkSyncPills
            clients={clients}
            showId={item.id}
            scopeType="show:all"
            scopeParams={null}
            profiles={profiles}
            childMediaItemIds={episodeIds}
            assignmentMap={assignmentMap}
          />
        )}
        onClick={() => setIsOpen((value) => !value)}
      />
      {isOpen && seasons.length > 0
        ? seasons.map((season) => (
            <SeasonRow
              key={season.id}
              season={season}
              showId={item.id}
              clients={clients}
              profiles={profiles}
              depth={depth + 1}
            />
          ))
        : null}
      {isOpen && seasons.length === 0 ? (
        <EpisodeRows
          episodes={episodes}
          clients={clients}
          profiles={profiles}
          depth={depth + 1}
          assetMap={assetMap}
          assignmentMap={assignmentMap}
        />
      ) : null}
    </>
  )
}

// ── Library section — fetches items when expanded ─────────────────────────────

function LibrarySection({
  library,
  clients,
  profiles,
  search,
}: {
  library: { id: string; title: string; type: string }
  clients: Client[]
  profiles: Profile[]
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
        pills={<StaticClientPills clients={clients} />}
        onClick={() => setIsOpen((value) => !value)}
      />
      {isOpen
        ? items.map((item) =>
            item.type === 'movie' ? (
              <MovieRow
                key={item.id}
                item={item}
                clients={clients}
                profiles={profiles}
                depth={1}
              />
            ) : (
              <ShowRow
                key={item.id}
                item={item}
                clients={clients}
                profiles={profiles}
                depth={1}
              />
            ),
          )
        : null}
    </div>
  )
}

// ── Main screen ───────────────────────────────────────────────────────────────

export function LibraryScreen() {
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

  const profilesQuery = useQuery({
    queryKey: ['profiles'],
    queryFn: getProfiles,
    staleTime: 60_000,
  })

  const libraries = librariesQuery.data ?? []
  const clients = clientsQuery.data ?? []
  const profiles = profilesQuery.data ?? []
  const isLoading =
    librariesQuery.isLoading || clientsQuery.isLoading || profilesQuery.isLoading
  const error = librariesQuery.error ?? clientsQuery.error ?? profilesQuery.error ?? null

  const clearVisible = Boolean(search.trim())

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
          <div className="inline-cluster">
            <SynInput
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search libraries or titles"
              prefixIcon={<IcoSearch />}
            />
            {clearVisible ? (
              <Btn size="small" onClick={() => setSearch('')}>
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
              profiles={profiles}
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
  pills?: React.ReactNode
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
  pills,
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
        {pills}
      </div>
    </div>
  )
}
