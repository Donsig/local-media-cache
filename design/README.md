# Handoff: Syncarr

## Overview
Syncarr is a homelab content-sync orchestration tool. It transcodes media from a source server (e.g. Plex) into per-client formats and pushes the right assets to each remote client (a phone, tablet, vacation-home server, camping rig, etc.) so they have local copies tuned to their device, network, and storage budget.

The design covers five primary screens:
1. **Client dashboard** — overview of every remote client, their storage, what's syncing
2. **Library** — browse the source content library as a 4-level fold-out tree (Library → Show/Movie → Season → Episode) with cascade sync toggles
3. **Profiles** — manage transcode presets (codec, resolution, GB/hr target)
4. **Content picker** — assign titles/seasons/episodes to clients
5. **Settings** — global config, server connections

## About the Design Files
The HTML file in this bundle is a **design reference** — a high-fidelity React + inline-CSS prototype showing the intended look and behavior. It is **not production code** to ship as-is. Your job is to **recreate this design in the target codebase** using its established patterns, component library, and conventions. If no codebase exists yet, pick a framework appropriate for the project (likely React/Next.js or Svelte for a homelab dashboard) and implement against that.

The prototype uses runtime-Babel-compiled JSX and ad-hoc inline styles for fast iteration; production should use a real component framework with proper styling primitives (CSS modules, Tailwind, vanilla-extract, styled-components — whatever the codebase uses).

## Fidelity
**High-fidelity.** All colors, typography, spacing, and interactions are final. Recreate pixel-perfectly, mapping the inline styles below to your codebase's design tokens / utility classes.

## Design Tokens

### Colors (warm slate dark)
```
bg0      #1a1815   page background
bg1      #1f1d1a   sidebar
bg2      #252220   card surface
bg3      #2e2b28   hover surface / row hover
bg4      #38342f   elevated controls
border   #34302c   default 1px borders
border2  #423d37   slightly stronger border (selected pills)

text0    #f5f0e8   primary text (warm white)
text1    #d4cdc1   secondary text
text2    #9b948a   tertiary / metadata
text3    #6e675f   muted / mono labels

accent       #c08552   warm terracotta / sand
accentDim    #c0855222   accent with ~13% alpha for selected pill backgrounds
accentText   #e6a472   accent with full saturation, used as text on accentDim

state.ready        #7ea672   sage green
state.transcoding  #d4a373   amber
state.queued       #8a8580   neutral
state.failed       #c87878   muted red
state.downloading  #7da3c8   slate blue
```

### Typography
- **Sans**: `'DM Sans', sans-serif` — UI text, headings, body
- **Mono**: `'DM Mono', monospace` — codes (S01E03), sizes (12.4 GB), timestamps, sectionlabels (uppercase, letter-spaced)
- Sizes: 10px (mono section labels) · 11px (mono metadata) · 12px (small UI) · 13px (default body) · 14px (titles in cards) · 15px (card headings) · 18px (screen H1) · 22px (large numerics)
- Weights: 400 (body) · 500 (medium) · 600 (semibold for titles)
- Section labels are mono uppercase with `letterSpacing: 0.1em`

### Spacing
- Container padding: `24px` (screen padding), `16px` (card interior)
- Tree row indent step: `20px` per depth level
- Gap scale: `4 / 6 / 8 / 10 / 12 / 14 / 16 / 20 / 24` px

### Radii
- `4px` — small buttons, sync pills
- `5px` — inputs, medium buttons
- `6-7px` — cards, list rows
- `8px` — large cards, sections

### Borders & Shadows
- 1px solid `#34302c` for almost all bordered surfaces
- No drop shadows — depth comes from background layering

## Screens

### 1. Client Dashboard (`section === 'clients'`)
**Purpose:** At-a-glance status of every remote client (paired peer device).

**Layout:** Vertical stack inside main panel (24px padding). One card per client, full-width.

**Each client card:**
- Header row: client name (16px semibold) + online dot (6px green/grey circle) + location subtitle + last-seen timestamp (mono 11px) on the right
- Storage bar: `<ProgressBar>` with used/total in GB (mono numerics, 22px)
- State chips row: ready / transcoding / queued / failed counts as `<Badge dot color={state}>` pills
- Active downloads list: pulsing arrow icon + asset title + progress %
- Actions row right-aligned: "View library", "Pause sync", "Settings"

### 2. Library (`section === 'assets'`) — THE KEY SCREEN
**Purpose:** Browse the source library, see transcode/sync state at every level, and toggle sync per-client with cascade behavior.

**Layout:**
- Sticky toolbar at top (16px 24px padding, bottom border):
  - Title "Library" + subtitle "{N} libraries · {M} assets"
  - State filter pills (`<PillTabs>`: All / Ready / Transcoding / Downloading / Queued / Failed)
  - Search input (`<SynInput>` with magnifying-glass prefix)
  - "Clear" button visible when filtering
- Scrolling tree below

**Tree structure (4 levels):**
```
Library (TV Shows, Movies, Cartoons, Danish TV-Series, Danish Movies)
└── Show or Movie (Severance, Dune Part Two, ...)
    └── Season  (only for shows; movies skip this level)
        └── Episode
            └── (expandable inline detail panel)
```

**Row anatomy at each level** (depth 0 = library, depth 3 = episode):
- Left: rotating chevron (`<IcoChevR>`, rotates 90° on open via `transform 0.15s`)
- Optional icon (TV / Film) for libraries and movies
- Title (depth-appropriate weight: 600 at lib, 500 at show, 400 at episode)
- Right cluster: stat badges, mono metadata (count, size, year), client sync pills
- Each row: `padding: 6px 16px 6px ${16 + depth*20}px`, `min-height: 38–40px`, hover `background: bg3`, bottom border `1px solid border`

**Sync toggle pills** (the key interaction):
- Rendered for every level (libraries, shows, seasons, episodes)
- One pill per client (Caravan, Holiday House, Camping Rig, ...)
- States:
  - **All-on**: solid accent background (`bg: accentDim`, `color: accentText`, `border: 1px solid accent`)
  - **Partial**: muted bg (`bg4`), text2 color, `~` indicator suffix — shown when *some* episodes underneath are synced to this client
  - **Off**: transparent bg, text3 color, default border
- Click cascades: clicking a pill at the library/show/season level toggles sync state for **every episode underneath** for that client. Clicking at episode level toggles just that one.
- Implementation: master `syncState` map of `{ episodeId → Set<clientName> }` lives at `<LibraryTree>`. The `cascadeSync(episodeIds, clientName, value)` function mutates the entire set in one update.

**Episode detail (inline expansion when an episode row is clicked):**
- Background `bg2`, padded, contains:
  - **Transcode profile**: select dropdown of available profiles
  - **Sync to clients**: per-client toggle pills (no cascade — just this episode)
  - **Size**: `{sourceGb} GB src → {outputGb} GB out` or `pending`, mono
  - **Transcoded**: timestamp (`2026-04-29 14:22`), mono
  - **Retry transcode** button (only if state === 'failed'), right-aligned, `<Btn variant="danger" small>`

**Filtering behavior:** When state filter or search is active, the tree filters in place — rows whose subtree contains zero matching episodes are hidden entirely. The chevron / cascade pills still work on whatever remains.

### 3. Profiles (`section === 'profiles'`)
Standard CRUD list of transcode presets. Each profile: name, type (quality-first / size-first / passthrough / custom), codec (H.264, H.265, AV1), resolution (4K / 1080p / 720p / 480p), gbPerHour estimate. New / Edit / Delete.

### 4. Content Picker (modal-style flow)
Multi-step assignment of library items to a client. Choose client → browse library → pick titles/seasons/episodes → assign profile → review → enqueue.

### 5. Settings
Global config: server URL, library scan interval, default profile, theme accent, network throttling rules.

## Interactions & Behavior

### Library tree
- Each row's chevron+row body is one click target — clicking anywhere expands/collapses
- Sync pill clicks must `stopPropagation()` so they don't toggle the row open/close
- Open state lives in a single `{ [id]: bool }` map at `<LibraryTree>`. IDs: `lib.id` for libraries, `item.id` for shows/movies, `${item.id}s${season.num}` for seasons, `ep-detail-${ep.id}` for episode detail panels
- Chevron rotation: `transform: ${open ? 'rotate(90deg)' : 'none'}` with `transition: transform 0.15s`

### Cascade sync
- "All-on" requires every episode under the parent to have that client in its sync set
- "Partial" requires at least one but not all
- Cascade write: when toggling partial/off → on, add client to **every** descendant; on → off removes from all

### Loading
- The HTML prototype shows a splash screen during Babel compile (~3-4s); production won't need this.

### Hover
- Rows: `bg → bg3` on hover, `transition: background 0.1s`
- Buttons: similar +1 step background lift

## State Management

```ts
type Episode = {
  id: string
  code: string         // "S01E03" or "Movie"
  title: string
  state: 'ready' | 'transcoding' | 'queued' | 'failed' | 'downloading'
  sourceGb: number
  outputGb?: number
  profile?: string
  clients: string[]    // initial sync state
  updated?: string     // ISO date
}

type Season = { num: number; episodes: Episode[] }
type LibraryItem = {
  id: string
  title: string
  year: number
  seasons?: Season[]   // absent for movies
  // when no seasons, the item itself acts as a single episode
}
type Library = { id: string; name: string; icon: 'tv' | 'film'; items: LibraryItem[] }

type Client = { id: string; name: string; online: boolean; storage: { usedGb, totalGb }; ... }
type Profile = { id: string; name: string; codec: string; resolution: string; gbPerHour: number; type: 'quality-first' | 'size-first' | 'passthrough' | 'custom' }

// derived/UI state at <LibraryTree>:
syncState: Record<EpisodeId, Set<ClientName>>
openMap: Record<NodeId, boolean>
globalFilter: 'all' | EpisodeState
globalSearch: string
```

Cascade write: `cascadeSync(episodeIds: string[], clientName: string, value: boolean)` does a single immutable update of `syncState`.

In the prototype the data is all hard-coded in `LIBRARY_DATA`. Production should fetch from the Syncarr backend (Plex scan + per-client sync DB).

## Components Inventory

The prototype defines these reusable atoms — recreate them in the target codebase's component library or use existing equivalents:

- `<Btn variant="primary|secondary|danger" small>` — button
- `<IconBtn>` — icon-only button
- `<Badge color="ready|transcoding|queued|failed|downloading|default" dot label>` — state pill
- `<ProgressBar value height>` — thin bar with optional label
- `<PillTabs tabs active onChange>` — segmented control of filter pills
- `<SynInput value onChange placeholder prefixIcon>` — text input
- Icons (inline SVG): `IcoTV`, `IcoFilm`, `IcoChevR`, `IcoSearch`, `IcoRetry`, `IcoPlus`, `IcoSettings`, ...

## Assets
No external image assets. All icons are inline SVG defined in the HTML. Replace with the codebase's icon library (Lucide, Heroicons, etc. — match the 16px stroke style).

Fonts (DM Sans, DM Mono) are loaded from Google Fonts in the prototype `<head>`. In production, self-host or use the codebase's font pipeline.

## Files in this bundle

- `Syncarr.html` — the full prototype, single file. Contains all five screens, the library tree, the tweaks panel, mock data, and inline styles. ~2000 lines, compiled in-browser via Babel for the prototype only.

## Recommended implementation path

1. **Scaffold** the app shell (sidebar nav + main content router) using the codebase's existing layout primitives.
2. **Define design tokens** from the table above in your theme system.
3. **Build atoms** (Btn, Badge, ProgressBar, PillTabs, IconBtn) — these recur on every screen.
4. **Implement the Library tree first** — it's the most complex screen and the sync-cascade logic is the core differentiator. Get `syncState` + `cascadeSync` right before anything else.
5. **Wire to backend** — replace `LIBRARY_DATA` with API calls, replace `syncState` mutations with PATCH requests.
6. **Other screens** (clients, profiles, settings, content picker) follow the same patterns — they're mostly cards + lists.
