# CLAUDE.md

Project conventions and context for Claude Code and Codex working on this codebase.

## Read first

Before changing any code or design:

1. `README.md` — project overview
2. `docs/ARCHITECTURE.md` — system design and component responsibilities
3. `docs/DOMAIN.md` — entities and state machines
4. `docs/DECISIONS.md` — why things are the way they are

If a request conflicts with a documented decision (in `docs/DECISIONS.md`), surface the conflict explicitly. Don't silently work around it.

## Implementation plans and environment context

Detailed implementation plans and environment-specific context (homelab IPs, Plex config, satellite hardware) live in the **Obsidian vault**, not this repo (public repo, private context).

**Vault location:** Obsidian vault — `~/workspace/Obsidian` on Linux, `C:\dev\repos\Obsidian` on Windows.

| What you need | Where to find it |
|---|---|
| Phase plan and task breakdown | `Lab/syncarr/plans/2026-05-04-phases-overview.md` |
| Current status and open blockers | `Lab/syncarr/index.md` |
| Environment facts (IPs, Plex config, hardware) | `Facts/Homelab/_index.md` |
| Architectural decisions with full context | `Lab/syncarr/decisions.md` |

> **Context.md**: the design handoff included a `docs/CONTEXT.md` with environment details. It is intentionally excluded from this repo — read `Facts/Homelab/_index.md` in the vault instead.

## Codex sub-agent

Use Codex (OpenAI gpt-5.5) for bulk implementation, test writing, and code review to conserve Claude tokens.

**Reference:** `Facts/Skills/installed/codex-agent.md` in the Obsidian vault for full usage patterns.

**Defaults:**
- Always use `codex exec --dangerously-bypass-approvals-and-sandbox`
- Default reasoning effort: `medium`
- Use the TASK / ALLOWED WRITES / NON-GOALS / STOP CONDITION / DONE MEANS prompt structure to prevent scope creep

## Working style

- Terse, technical communication. No filler.
- Push back on bad ideas. Critical feedback is more valuable than polite agreement.
- When uncertain, say so or check docs — don't guess with confidence.
- Cite sources for technical claims with real consequences (library behaviors, version constraints, config syntax).

## When working on the code

- **Verify before recall**: check actual current docs for `python-plexapi`, `aria2p`, `FastAPI`, `SQLAlchemy`. APIs change.
- **Provider abstraction**: media-server-specific code belongs in `providers/` only. Don't import `plexapi` outside `PlexProvider`.
- **Small, reviewable changes**: multiple focused commits over giant ones.
- **Tests for state machines**: resolver, assignment state transitions, eviction flow — tests make refactoring safe.
- **Don't add dependencies casually**: stdlib first.

## Stack

- **Server**: Python 3.12, FastAPI, SQLAlchemy 2.x async, SQLite (WAL), python-plexapi, httpx, structlog
- **Agent**: Python 3.12, aria2p, httpx, sqlite3
- **External binaries**: ffmpeg (server), aria2c (agent)
- **Dev**: ruff, pytest, mypy strict

## Conventions

- Async everywhere on the server
- Pydantic models for all API request/response bodies
- Type hints everywhere, mypy strict
- One module per route file, grouped by audience (`agent.py`, `ui.py`, `media_browse.py`)
- DB access through repository functions, not raw queries in routes
- All times UTC, ISO 8601 in JSON, TIMESTAMP in SQLite
- Bytes (integers) for sizes internally; format for display in UI only
- Logging: `structlog`, human-readable in dev, JSON in prod

## Scope discipline

MVP is intentionally narrow. See `docs/MVP.md` for what's in scope and `docs/DEFERRED.md` for what's not. If asked to add something in DEFERRED, surface the conflict before implementing.

## Things to flag, not silently fix

- Anything reducing failure-mode coverage from `docs/ARCHITECTURE.md` "Resilience model"
- Anything changing a documented invariant from `docs/ARCHITECTURE.md` "Key invariants"
- Adding writes to the media server's database (read-only via provider API only)
- Adding agent-side state that contradicts the server-as-truth model
- Media-server-specific code leaking outside `providers/`
